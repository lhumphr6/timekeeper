import os
from datetime import datetime, date, timedelta
from typing import Optional, List, Set
from decimal import Decimal, ROUND_HALF_UP

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, text, Column, Integer, Date, String, DateTime
from sqlalchemy.orm import Session

from .db import engine, SessionLocal, get_session, ping_db
from .models import (
    Base,
    User,
    Employee,
    TimeEntry,
    WeekAssignment,
    TimesheetStatus,
    EmployeePeriodSetting,
    TimesheetPeriod,
    DuplicateReview,
    PTOAccount,
    PTOAdjustment,
)
from .auth import hash_password, verify_and_update_password, login_required
from .process_excel import import_workbook
from .utils import (
    D,
    q2,
    group_entries_for_timesheet,
    enumerate_timesheets_global,
    _semi_monthly_period_for_date,
)

# Attendance router (keeps this file small)
from .attendance import router as attendance_router
from .dept_importer import router as dept_importer_router

SECRET_KEY = os.getenv("SECRET_KEY", "please-change-me")
DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "Admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "1Senior!")
PORT = int(os.getenv("PORT", "5070"))

app = FastAPI(title="TimeKeeper")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
# Make templates accessible to submodules (e.g., attendance)
app.state.templates = templates

# Smart loader: prefer UTF-8, fallback to cp1252 for Windows-saved files
try:
    from jinja2.loaders import FileSystemLoader

    class SmartLoader(FileSystemLoader):
        def get_source(self, environment, template):
            try:
                self.encoding = "utf-8"
                return super().get_source(environment, template)
            except UnicodeDecodeError:
                self.encoding = "cp1252"
                return super().get_source(environment, template)

    templates.env.loader = SmartLoader("app/templates")
except Exception:
    pass

# -----------------------
# Jinja filters (formatting)
# -----------------------
def fmt2(x):
    if x is None:
        return "0.00"
    try:
        d = Decimal(str(x))
    except Exception:
        return str(x)
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def fmt_time(x):
    if not x:
        return ""
    try:
        if isinstance(x, datetime):
            return x.strftime("%I:%M:%S %p")
        s = str(x)
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%H:%M:%S.%f", "%H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%I:%M:%S %p")
            except Exception:
                continue
        base = s.split(".")[0]
        parts = base.split()
        tpart = parts[-1] if parts else base
        try:
            dt = datetime.strptime(tpart, "%H:%M:%S")
            return dt.strftime("%I:%M:%S %p")
        except Exception:
            return tpart
    except Exception:
        return str(x)

def fmt_dt(x):
    if not x:
        return ""
    try:
        if isinstance(x, datetime):
            return x.strftime("%b %d, %Y %I:%M:%S %p")
        s = str(x)
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%b %d, %Y %I:%M:%S %p")
            except Exception:
                continue
        return s
    except Exception:
        return str(x)

def fmt_excel_dt(x):
    # Excel style "MM/DD/YYYY hh:mm AM/PM"
    if not x:
        return ""
    try:
        if isinstance(x, datetime):
            return x.strftime("%m/%d/%Y %I:%M %p")
        s = str(x).strip()
        fmts = [
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%y %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
        ]
        for fmt in fmts:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%m/%d/%Y %I:%M %p")
            except Exception:
                continue
        return s
    except Exception:
        return str(x)

templates.env.filters["fmt2"] = fmt2
templates.env.filters["fmt_time"] = fmt_time
templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["fmt_excel_dt"] = fmt_excel_dt

# -----------------------
# Review tables (dismiss banners without altering entries)
# -----------------------
class LongShiftFlag(Base):
    """
    Dates flagged for long shifts (>= 10 hours). Edits do not clear these automatically.
    Clearing requires clicking the Reviewed button.
    """
    __tablename__ = "long_shift_flags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

class LongShiftReview(Base):
    __tablename__ = "long_shift_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

# PTO review flow: dates that need PTO review are flagged, and remain highlighted until reviewed
class PtoNeedFlag(Base):
    __tablename__ = "pto_need_flags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

class PtoReviewFlag(Base):
    __tablename__ = "pto_review_flags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

# -----------------------
# Admin role + profile tables (isolated; no change to User schema)
# -----------------------
class AdminUser(Base):
    __tablename__ = "admin_users"
    user_id = Column(Integer, primary_key=True, index=True)

class UserProfile(Base):
    __tablename__ = "user_profiles"
    user_id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=True)

# -----------------------
# PTO usage exclusions (to hide usage rows without altering timesheets)
# -----------------------
class PTOUsageExclusion(Base):
    __tablename__ = "pto_usage_exclusions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    pto_type = Column(String(64), nullable=True)  # match specific PTO type, NULL matches NULL PTO type
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

def ensure_schema():
    with engine.connect() as conn:
        # Backup columns for times hidden by PTO status in print view
        conn.execute(text("ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS pto_clock_in_backup TIMESTAMP NULL"))
        conn.execute(text("ALTER TABLE time_entries ADD COLUMN IF NOT EXISTS pto_clock_out_backup TIMESTAMP NULL"))

        # Year columns for per-year PTO tracking
        conn.execute(text("ALTER TABLE pto_accounts ADD COLUMN IF NOT EXISTS year INTEGER"))
        conn.execute(text("ALTER TABLE pto_adjustments ADD COLUMN IF NOT EXISTS year INTEGER"))

        # Backfill existing rows to current year if null
        conn.execute(text("UPDATE pto_accounts SET year = EXTRACT(YEAR FROM NOW())::INTEGER WHERE year IS NULL"))
        conn.execute(text("UPDATE pto_adjustments SET year = EXTRACT(YEAR FROM NOW())::INTEGER WHERE year IS NULL"))

        # SAFEGUARD: Drop incorrect unique index that enforces only one row per employee
        # This index conflicts with per-year balances and caused the error you saw.
        conn.execute(text("DROP INDEX IF EXISTS ix_pto_accounts_employee_id"))

        # Enforce one starting balance per employee per year (correct composite uniqueness)
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_pto_accounts_emp_year ON pto_accounts (employee_id, year)"))

        # Employee active status + termination date
        conn.execute(text("ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
        conn.execute(text("ALTER TABLE employees ADD COLUMN IF NOT EXISTS termination_date DATE NULL"))
        conn.execute(text("UPDATE employees SET is_active = TRUE WHERE is_active IS NULL"))

        conn.commit()

@app.on_event("startup")
def on_startup():
    try:
        ping_db()
        Base.metadata.create_all(bind=engine)
        ensure_schema()
        with SessionLocal() as s:
            default = s.query(User).filter(User.username == DEFAULT_ADMIN_USER).first()
            if not default:
                default = User(username=DEFAULT_ADMIN_USER, password_hash=hash_password(DEFAULT_ADMIN_PASSWORD))
                s.add(default)
                s.flush()
            if not s.query(AdminUser).filter(AdminUser.user_id == default.id).first():
                s.add(AdminUser(user_id=default.id))
            s.commit()
        print("[startup] Timekeeper ready.")
    except Exception as ex:
        print(f"[startup] Database initialization failed: {ex}")

def current_is_admin(db: Session, user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    return bool(db.query(AdminUser).filter(AdminUser.user_id == user_id).first())

def require_admin_edit(request: Request, db: Session):
    """
    Enforce view/print-only for non-admin users on any write action.
    """
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Read-only user: edit action not permitted")

@app.get("/health")
def health():
    try:
        ping_db()
        return JSONResponse({"status": "ok"})
    except Exception as ex:
        return JSONResponse({"status": "error", "detail": str(ex)}, status_code=500)

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/viewer")
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "hide_nav_links": True})

@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_session),
):
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            return templates.TemplateResponse("login.html", {"request": request, "hide_nav_links": True, "error": "Invalid credentials"}, status_code=401)

        verified, new_hash = verify_and_update_password(password, user.password_hash)
        if not verified:
            return templates.TemplateResponse("login.html", {"request": request, "hide_nav_links": True, "error": "Invalid credentials"}, status_code=401)

        if new_hash:
            user.password_hash = new_hash
            db.commit()

        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["is_admin"] = current_is_admin(db, user.id)
        return RedirectResponse(url="/viewer", status_code=303)
    except Exception as ex:
        print(f"[login] error: {ex}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "hide_nav_links": True, "error": f"Login failed due to server/database error: {ex}"},
            status_code=500,
        )

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

# -------- Upload --------
@app.get("/upload", response_class=HTMLResponse)
@login_required
def upload_page(request: Request):
    # View/print-only users cannot access importer UI
    with SessionLocal() as db:
        if not current_is_admin(db, request.session.get("user_id")):
            raise HTTPException(status_code=403, detail="Admin access required")
    return templates.TemplateResponse("upload.html", {"request": request})

@app.post("/upload")
@login_required
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    timesheet_name: str = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    allowed_ext = (".xlsx", ".xlsm", ".xls")
    if not file.filename.lower().endswith(allowed_ext):
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "error": f"Unsupported file type. Please upload one of: {', '.join(allowed_ext)}"},
            status_code=400,
        )

    name = (timesheet_name or "").strip()
    if not name:
        return templates.TemplateResponse(
            "upload.html", {"request": request, "error": "Time Period Name is required."}, status_code=400
        )

    try:
        contents = await file.read()
        os.makedirs("uploads", exist_ok=True)
        saved_path = os.path.join("uploads", f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{file.filename}")
        with open(saved_path, "wb") as f:
            f.write(contents)

        placeholder_date = date.today()
        ts = TimesheetPeriod(period_start=placeholder_date, period_end=placeholder_date, name=name)
        db.add(ts)
        db.flush()
        new_id = ts.id

        try:
            import_workbook(saved_path, db, timesheet_id=new_id)
        except TypeError:
            import_workbook(saved_path, db)

        min_date, max_date = (
            db.query(func.min(TimeEntry.work_date), func.max(TimeEntry.work_date))
            .filter(TimeEntry.timesheet_id == new_id)
            .one()
        )

        if not min_date:
            db.delete(ts)
            db.commit()
            return templates.TemplateResponse(
                "upload.html", {"request": request, "error": "No rows were imported. Please check the workbook."}, status_code=400
            )

        ps1, pe1 = _semi_monthly_period_for_date(min_date)
        ps2, pe2 = _semi_monthly_period_for_date(max_date or min_date)
        ps, pe = (ps2, pe2) if (ps1, pe1) != (ps2, pe2) else (ps1, pe1)

        ts.period_start = ps
        ts.period_end = pe
        db.commit()

        return RedirectResponse(url=f"/assign-weeks?timesheet_id={new_id}", status_code=303)

    except Exception as ex:
        print(f"[upload] import failed: {ex}")
        return templates.TemplateResponse("upload.html", {"request": request, "error": f"Import failed: {ex}"}, status_code=500)

# -------- Assign weeks --------
@app.get("/assign-weeks", response_class=HTMLResponse)
@login_required
def assign_weeks_page(
    request: Request,
    timesheet_id: int = Query(...),
    db: Session = Depends(get_session),
):
    # Only admins can assign weeks
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    days = [
        r[0]
        for r in (
            db.query(TimeEntry.work_date)
            .filter(TimeEntry.timesheet_id == timesheet_id)
            .group_by(TimeEntry.work_date)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
    ]
    existing = {
        wa.day_date: wa.week_number
        for wa in db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    }

    return templates.TemplateResponse(
        "assign_weeks.html",
        {
            "request": request,
            "timesheet_id": timesheet_id,
            "period": f"{ts.period_start.isoformat()}..{ts.period_end.isoformat()}",
            "days": days,
            "existing": existing,
            "timesheet_name": ts.name or "",
        },
    )

@app.post("/assign-weeks")
@login_required
async def assign_weeks_submit(
    request: Request,
    timesheet_id: int = Form(...),
    timesheet_name: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    form = await request.form()
    for k, v in form.items():
        if not k.startswith("week_"):
            continue
        day_str = k[len("week_") :]
        try:
            day = date.fromisoformat(day_str)
            week = int(v)
        except Exception:
            continue

        wa = (
            db.query(WeekAssignment)
            .filter(WeekAssignment.timesheet_id == timesheet_id, WeekAssignment.day_date == day)
            .first()
        )
        if not wa:
            db.add(
                WeekAssignment(
                    timesheet_id=timesheet_id,
                    period_start=ts.period_start,
                    period_end=ts.period_end,
                    day_date=day,
                    week_number=week,
                )
            )
        else:
            wa.week_number = week

    if timesheet_name is not None:
        ts.name = (timesheet_name or "").strip() or None

    db.commit()
    return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}", status_code=303)

# -------- Entry editing (Decimal-safe) --------
def _to_dec_opt(v: Optional[str]) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return q2(D(v))
    except Exception:
        return None

@app.post("/timesheet/update-entry")
@login_required
def update_entry(
    request: Request,
    entry_id: int = Form(...),
    timesheet_id: int = Form(...),
    employee_id: Optional[int] = Form(None),
    total_hours: Optional[str] = Form(None),
    break_hours: Optional[str] = Form(None),
    pto_hours: Optional[str] = Form("0"),
    pto_type: Optional[str] = Form(""),
    holiday_hours: Optional[str] = Form(None),
    bereavement_hours: Optional[str] = Form(None),
    redirect_to: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    entry = db.query(TimeEntry).get(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    th = _to_dec_opt(total_hours)
    bh = _to_dec_opt(break_hours)
    ph = _to_dec_opt(pto_hours)
    hh = _to_dec_opt(holiday_hours)
    oh = _to_dec_opt(bereavement_hours)

    if th is not None:
        entry.total_hours = th
    if bh is not None:
        entry.break_hours = bh
    if hh is not None:
        entry.holiday_hours = hh
    if oh is not None:
        entry.bereavement_hours = oh

    new_pto_type = (pto_type or "").strip() or None

    # If PTO type is set, hide CI/CO by moving them to backup cols
    if new_pto_type and not entry.pto_type:
        if entry.clock_in:
            entry.pto_clock_in_backup = entry.clock_in
        if entry.clock_out:
            entry.pto_clock_out_backup = entry.clock_out
        entry.clock_in = None
        entry.clock_out = None

    # If PTO type cleared, restore CI/CO from backup
    if not new_pto_type and entry.pto_type:
        if entry.pto_clock_in_backup:
            entry.clock_in = entry.pto_clock_in_backup
        if entry.pto_clock_out_backup:
            entry.clock_out = entry.pto_clock_out_backup

    entry.pto_type = new_pto_type
    if ph is not None:
        entry.pto_hours = ph

    worked = D(entry.total_hours or 0) - D(entry.break_hours or 0)
    if worked < D(0):
        worked = D(0)
    entry.hours_paid = q2(
        worked + D(entry.pto_hours or 0) + D(entry.holiday_hours or 0) + D(entry.bereavement_hours or 0)
    )

    db.commit()

    if redirect_to:
        return RedirectResponse(url=redirect_to, status_code=303)

    suffix = f"?timesheet_id={timesheet_id}"
    if employee_id:
        suffix += f"&employee_id={employee_id}"
    return RedirectResponse(url=f"/viewer{suffix}", status_code=303)

# -------- Clock editing (Decimal-safe, JSON for instant UI updates) --------
def _parse_dt_local(v: Optional[str]) -> Optional[datetime]:
    """
    Parse HTML datetime-local values safely:
    - 'YYYY-MM-DDTHH:MM'
    - 'YYYY-MM-DDTHH:MM:SS'
    Returns None for blank/None.
    """
    if not v:
        return None
    v = v.strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None

@app.post("/timesheet/update-clocks")
@login_required
async def update_clocks(
    request: Request,
    entry_id: int = Form(...),
    clock_in: Optional[str] = Form(None),
    clock_out: Optional[str] = Form(None),
    # Persist other numeric fields in the same submit (keep paid total in sync)
    break_hours: Optional[str] = Form(None),
    pto_hours: Optional[str] = Form(None),
    holiday_hours: Optional[str] = Form(None),
    bereavement_hours: Optional[str] = Form(None),
    redirect_to: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    accept = (request.headers.get("accept") or "").lower()

    # Only admins can edit clocks
    if not current_is_admin(db, request.session.get("user_id")):
        if "application/json" in accept:
            return JSONResponse({"ok": False, "error": "admin_required"}, status_code=403)
        raise HTTPException(status_code=403, detail="Read-only user: edit action not permitted")

    entry = db.query(TimeEntry).get(entry_id)
    if not entry:
        if "application/json" in accept:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return RedirectResponse(url=redirect_to or "/", status_code=303)

    new_ci = _parse_dt_local(clock_in)
    new_co = _parse_dt_local(clock_out)

    if new_ci is not None:
        entry.clock_in = new_ci
    if new_co is not None:
        entry.clock_out = new_co

    # Overnight: if both set and out <= in, assume next day
    if entry.clock_in and entry.clock_out and entry.clock_out <= entry.clock_in:
        entry.clock_out = entry.clock_out + timedelta(days=1)

    # Auto-recalculate total from clocks when both present (Decimal-safe)
    if entry.clock_in and entry.clock_out:
        entry.total_hours = q2(D((entry.clock_out - entry.clock_in).total_seconds()) / D(3600))

    # Also persist any numeric field edits from the same form
    bh = _to_dec_opt(break_hours)
    ph = _to_dec_opt(pto_hours)
    hh = _to_dec_opt(holiday_hours)
    oh = _to_dec_opt(bereavement_hours)

    if bh is not None:
        entry.break_hours = bh
    if ph is not None:
        entry.pto_hours = ph
    if hh is not None:
        entry.holiday_hours = hh
    if oh is not None:
        entry.bereavement_hours = oh

    worked = D(entry.total_hours or 0) - D(entry.break_hours or 0)
    if worked < D(0):
        worked = D(0)
    entry.hours_paid = q2(
        worked + D(entry.pto_hours or 0) + D(entry.holiday_hours or 0) + D(entry.bereavement_hours or 0)
    )

    db.commit()

    # If called via AJAX (Accept: application/json), return formatted values for instant UI update
    if "application/json" in accept:
        return JSONResponse({
            "ok": True,
            "clock_in_fmt": fmt_excel_dt(entry.clock_in) if entry.clock_in else None,
            "clock_out_fmt": fmt_excel_dt(entry.clock_out) if entry.clock_out else None,
            "total_hours": float(D(entry.total_hours or 0)),
            "total_hours_fmt": fmt2(entry.total_hours or 0),
            "hours_paid": float(D(entry.hours_paid or 0)),
            "hours_paid_fmt": fmt2(entry.hours_paid or 0),
        })

    # Otherwise, follow normal redirect flow
    return RedirectResponse(url=redirect_to or "/", status_code=303)

# -------- Employee-period settings --------
@app.post("/viewer/update-employee-period")
@login_required
def update_employee_period(
    request: Request,
    employee_id: int = Form(...),
    timesheet_id: int = Form(...),
    carry_over_hours: Optional[str] = Form("0"),
    redirect_to: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    eps = (
        db.query(EmployeePeriodSetting)
        .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == employee_id)
        .first()
    )
    if not eps:
        eps = EmployeePeriodSetting(
            employee_id=employee_id,
            period_start=ts.period_start,
            period_end=ts.period_end,
            timesheet_id=timesheet_id,
        )
        db.add(eps)

    eps.carry_over_hours = q2(D(carry_over_hours or "0"))

    db.commit()
    if redirect_to:
        return RedirectResponse(url=redirect_to, status_code=303)
    return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}&employee_id={employee_id}", status_code=303)

# -------- Submit timesheet --------
@app.post("/viewer/submit")
@login_required
def submit_timesheet(
    request: Request,
    employee_id: int = Form(...),
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    row = (
        db.query(TimesheetStatus)
        .filter(TimesheetStatus.timesheet_id == timesheet_id, TimesheetStatus.employee_id == employee_id)
        .first()
    )
    if not row:
        db.add(
            TimesheetStatus(
                timesheet_id=timesheet_id,
                employee_id=employee_id,
                period_start=ts.period_start,
                period_end=ts.period_end,
                status="submitted",
            )
        )
    else:
        row.status = "submitted"
    db.commit()

    # Determine next pending employee
    employees_all = db.query(Employee).order_by(Employee.name.asc()).all()
    emp_ids_with_rows = [
        r[0]
        for r in db.query(TimeEntry.employee_id)
        .filter(TimeEntry.timesheet_id == timesheet_id)
        .group_by(TimeEntry.employee_id)
        .all()
    ]
    submitted = {
        r[0]: r[1]
        for r in db.query(TimesheetStatus.employee_id, TimesheetStatus.status)
        .filter(TimesheetStatus.timesheet_id == timesheet_id)
        .all()
    }
    pending_ids = [eid for eid in emp_ids_with_rows if eid not in submitted or submitted[eid] != "submitted"]

    next_employee_id: Optional[int] = None
    current_index = next((i for i, e in enumerate(employees_all) if e.id == employee_id), None)
    if current_index is not None:
        for e in employees_all[current_index + 1 :]:
            if e.id in pending_ids:
                next_employee_id = e.id
                break

    if next_employee_id:
        return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}&employee_id={next_employee_id}", status_code=303)

    if len(pending_ids) == 0:
        msg = "You are all done with this timeperiod, See you next time!"
        return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}&include_submitted=1&msg={msg}", status_code=303)

    first_pending = next((e.id for e in employees_all if e.id in pending_ids), None)
    if first_pending:
        return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}&employee_id={first_pending}", status_code=303)

    return RedirectResponse(url=f"/viewer?timesheet_id={timesheet_id}", status_code=303)

# -------- Keep duplicates --------
@app.post("/viewer/keep-duplicates")
@login_required
def viewer_keep_duplicates(
    request: Request,
    employee_id: int = Form(...),
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    dup_rows = (
        db.query(TimeEntry.work_date, func.count(TimeEntry.id))
        .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == employee_id)
        .group_by(TimeEntry.work_date)
        .having(func.count(TimeEntry.id) > 1)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )
    dup_dates = [d for d, _ in dup_rows]

    created = 0
    for d in dup_dates:
        exists = (
            db.query(DuplicateReview)
            .filter(
                DuplicateReview.timesheet_id == timesheet_id,
                DuplicateReview.employee_id == employee_id,
                DuplicateReview.work_date == d,
            )
            .first()
        )
        if not exists:
            db.add(DuplicateReview(timesheet_id=timesheet_id, employee_id=employee_id, work_date=d))
            created += 1
    if created:
        db.commit()

    msg = "Duplicate dates marked as reviewed."
    return RedirectResponse(
        url=f"/viewer?timesheet_id={timesheet_id}&employee_id={employee_id}&msg={msg}",
        status_code=303,
    )

# -------- Review long shifts --------
@app.post("/viewer/review-long-shifts")
@login_required
def viewer_review_long_shifts(
    request: Request,
    employee_id: int = Form(...),
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    flagged_dates = {
        r.work_date
        for r in db.query(LongShiftFlag)
        .filter(LongShiftFlag.timesheet_id == timesheet_id, LongShiftFlag.employee_id == employee_id)
        .all()
    }

    created = 0
    for d in flagged_dates:
        exists = (
            db.query(LongShiftReview)
            .filter(
                LongShiftReview.timesheet_id == timesheet_id,
                LongShiftReview.employee_id == employee_id,
                LongShiftReview.work_date == d,
            )
            .first()
        )
        if not exists:
            db.add(LongShiftReview(timesheet_id=timesheet_id, employee_id=employee_id, work_date=d))
            created += 1
    if created:
        db.commit()

    msg = "Long shifts marked as reviewed."
    return RedirectResponse(
        url=f"/viewer?timesheet_id={timesheet_id}&employee_id={employee_id}&msg={msg}",
        status_code=303,
    )

# -------- Review PTO needs --------
@app.post("/viewer/review-pto-needs")
@login_required
def viewer_review_pto_needs(
    request: Request,
    employee_id: int = Form(...),
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    flagged_dates = {
        r.work_date
        for r in db.query(PtoNeedFlag)
        .filter(PtoNeedFlag.timesheet_id == timesheet_id, PtoNeedFlag.employee_id == employee_id)
        .all()
    }

    created = 0
    for d in flagged_dates:
        exists = (
            db.query(PtoReviewFlag)
            .filter(
                PtoReviewFlag.timesheet_id == timesheet_id,
                PtoReviewFlag.employee_id == employee_id,
                PtoReviewFlag.work_date == d,
            )
            .first()
        )
        if not exists:
            db.add(PtoReviewFlag(timesheet_id=timesheet_id, employee_id=employee_id, work_date=d))
            created += 1
    if created:
        db.commit()

    msg = "PTO review marked as reviewed."
    return RedirectResponse(
        url=f"/viewer?timesheet_id={timesheet_id}&employee_id={employee_id}&msg={msg}",
        status_code=303,
    )

# -------- Delete time period --------
@app.post("/viewer/delete-period")
@login_required
def delete_period_post(
    request: Request,
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)

    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    # Delete all rows related to this period
    db.query(TimeEntry).filter(TimeEntry.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(TimesheetStatus).filter(TimesheetStatus.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(EmployeePeriodSetting).filter(EmployeePeriodSetting.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(DuplicateReview).filter(DuplicateReview.timesheet_id == timesheet_id).delete(synchronize_session=False)
    # Clear review/flag tables
    db.query(LongShiftFlag).filter(LongShiftFlag.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(LongShiftReview).filter(LongShiftReview.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(PtoNeedFlag).filter(PtoNeedFlag.timesheet_id == timesheet_id).delete(synchronize_session=False)
    db.query(PtoReviewFlag).filter(PtoReviewFlag.timesheet_id == timesheet_id).delete(synchronize_session=False)

    db.delete(ts)
    db.commit()

    return RedirectResponse(url="/viewer?msg=Time+Period+deleted", status_code=303)

# -----------------------
# Admin: Employee status management
# -----------------------
@app.get("/admin/employees", response_class=HTMLResponse)
@login_required
def admin_employees_page(
    request: Request,
    include_inactive: int = Query(1),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    if include_inactive:
        employees = db.query(Employee).order_by(Employee.name.asc()).all()
    else:
        employees = db.query(Employee).filter(text("COALESCE(is_active, TRUE) = TRUE")).order_by(Employee.name.asc()).all()

    inactive_ids = {r[0] for r in db.execute(text("SELECT id FROM employees WHERE COALESCE(is_active, TRUE) = FALSE")).fetchall()}

    return templates.TemplateResponse(
        "admin_employees.html",
        {
            "request": request,
            "employees": employees,
            "include_inactive": include_inactive,
            "inactive_ids": inactive_ids,
        },
    )

@app.post("/admin/employees/set-status")
@login_required
def admin_employees_set_status(
    request: Request,
    employee_id: int = Form(...),
    is_active: int = Form(...),  # 1 or 0
    termination_date: Optional[str] = Form(None),
    redirect_to: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    emp.is_active = bool(int(is_active))
    if not emp.is_active:
        td = (termination_date or "").strip()
        if td:
            try:
                emp.termination_date = date.fromisoformat(td)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid termination_date")
        else:
            emp.termination_date = None
    else:
        emp.termination_date = None

    db.commit()
    return RedirectResponse(url=(redirect_to or "/admin/employees"), status_code=303)

# -----------------------
# Admin: Users page
# -----------------------
@app.get("/admin/users", response_class=HTMLResponse)
@login_required
def admin_users_page(
    request: Request,
    msg: Optional[str] = None,
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    users = db.query(User).order_by(User.username.asc()).all()
    admin_ids = {r.user_id for r in db.query(AdminUser).all()}
    profiles = {r.user_id: (r.full_name or "") for r in db.query(UserProfile).all()}
    admin_count = len(admin_ids)

    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "users": users,
            "admin_ids": admin_ids,
            "profiles": profiles,
            "admin_count": admin_count,
            "me_id": request.session.get("user_id"),
            "flash": msg,
        },
    )

@app.post("/admin/users/create")
@login_required
def admin_users_create(
    request: Request,
    full_name: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    uname = (username or "").strip()
    pwd = (password or "").strip()
    role = (role or "user").strip().lower()
    fname = (full_name or "").strip()

    if not uname or not pwd:
        return RedirectResponse(url="/admin/users?msg=Username+and+password+required", status_code=303)
    if db.query(User).filter(User.username == uname).first():
        return RedirectResponse(url="/admin/users?msg=Username+already+exists", status_code=303)

    u = User(username=uname, password_hash=hash_password(pwd))
    db.add(u)
    db.flush()
    db.add(UserProfile(user_id=u.id, full_name=fname or None))
    if role == "admin":
        db.add(AdminUser(user_id=u.id))
    db.commit()
    return RedirectResponse(url="/admin/users?msg=User+created", status_code=303)

@app.post("/admin/users/reset-password")
@login_required
def admin_users_reset_password(
    request: Request,
    user_id: int = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    u = db.query(User).get(user_id)
    if not u:
        return RedirectResponse(url="/admin/users?msg=User+not+found", status_code=303)
    pwd = (new_password or "").strip()
    if not pwd:
        return RedirectResponse(url="/admin/users?msg=Password+required", status_code=303)
    u.password_hash = hash_password(pwd)
    db.commit()
    return RedirectResponse(url="/admin/users?msg=Password+reset", status_code=303)

@app.post("/admin/users/update-role")
@login_required
def admin_users_update_role(
    request: Request,
    user_id: int = Form(...),
    role: str = Form(...),  # "admin" (demotion disabled)
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    role = (role or "user").strip().lower()
    target = db.query(User).get(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?msg=User+not+found", status_code=303)

    is_admin_now = bool(db.query(AdminUser).filter(AdminUser.user_id == user_id).first())

    if role == "user":
        # Demotion disabled per requirements
        return RedirectResponse(url="/admin/users?msg=Demotion+disabled", status_code=303)

    # Promote to admin if not already
    if not is_admin_now:
        db.add(AdminUser(user_id=user_id))
        db.commit()
        return RedirectResponse(url="/admin/users?msg=Promoted+to+admin", status_code=303)

    return RedirectResponse(url="/admin/users?msg=Already+admin", status_code=303)

@app.post("/admin/users/delete")
@login_required
def admin_users_delete(
    request: Request,
    user_id: int = Form(...),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    if user_id == request.session.get("user_id"):
        return RedirectResponse(url="/admin/users?msg=Cannot+delete+the+current+user", status_code=303)

    target = db.query(User).get(user_id)
    if not target:
        return RedirectResponse(url="/admin/users?msg=User+not+found", status_code=303)

    db.query(AdminUser).filter(AdminUser.user_id == user_id).delete(synchronize_session=False)
    db.query(UserProfile).filter(UserProfile.user_id == user_id).delete(synchronize_session=False)
    db.delete(target)
    db.commit()
    return RedirectResponse(url="/admin/users?msg=User+deleted", status_code=303)

# -------- Viewer (Timesheet Editor)
@app.get("/viewer", response_class=HTMLResponse)
@login_required
def viewer_page(
    request: Request,
    timesheet_id: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
    include_submitted: int = 0,
    msg: Optional[str] = None,
    db: Session = Depends(get_session),
):
    employees_all = db.query(Employee).order_by(Employee.name.asc()).all()

    sheets = enumerate_timesheets_global(db)
    period_options = [{"timesheet_id": tid, "start": ps, "end": pe, "display": name} for tid, ps, pe, name in sheets]
    if not period_options:
        return templates.TemplateResponse(
            "viewer.html",
            {
                "request": request,
                "employees": [],
                "selected_employee": None,
                "period_options": [],
                "active_ts": None,
                "grouped": None,
                "employee_setting": {"carry_over_hours": 0.0},
                "duplicates": [],
                "dup_dates": set(),
                "flash": "No timesheet instances found.",
                "all_done": False,
                "can_edit": bool(request.session.get("is_admin")),
            },
        )

    ts = db.query(TimesheetPeriod).get(timesheet_id) if timesheet_id else db.query(TimesheetPeriod).get(period_options[-1]["timesheet_id"])
    if not ts:
        ts = db.query(TimesheetPeriod).get(period_options[-1]["timesheet_id"])
    active_ts_id = ts.id

    done_message = "You are all done with this timeperiod, See you next time!"
    if msg and done_message in msg:
        return templates.TemplateResponse(
            "viewer.html",
            {
                "request": request,
                "employees": [],
                "selected_employee": None,
                "period_options": period_options,
                "active_ts": active_ts_id,
                "grouped": None,
                "employee_setting": {"carry_over_hours": 0.0},
                "duplicates": [],
                "dup_dates": set(),
                "flash": msg,
                "all_done": True,
                "can_edit": bool(request.session.get("is_admin")),
            },
        )

    # Normal editor view
    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == active_ts_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}
    if not week_map:
        return RedirectResponse(url=f"/assign-weeks?timesheet_id={active_ts_id}", status_code=303)

    emp_ids_with_rows = [
        r[0] for r in db.query(TimeEntry.employee_id).filter(TimeEntry.timesheet_id == active_ts_id).group_by(TimeEntry.employee_id).all()
    ]
    submitted = {
        r[0]: r[1]
        for r in db.query(TimesheetStatus.employee_id, TimesheetStatus.status).filter(TimesheetStatus.timesheet_id == active_ts_id).all()
    }
    pending_ids = [eid for eid in emp_ids_with_rows if eid not in submitted or submitted[eid] != "submitted"]
    employees = [e for e in employees_all if (e.id in pending_ids) or (include_submitted and e.id in submitted)]
    selected_employee = db.query(Employee).get(employee_id) if employee_id else (employees[0] if employees else (employees_all[0] if employees_all else None))

    carry = 0.0
    if selected_employee:
        eps = (
            db.query(EmployeePeriodSetting)
            .filter(EmployeePeriodSetting.timesheet_id == active_ts_id, EmployeePeriodSetting.employee_id == selected_employee.id)
            .first()
        )
        carry = float(eps.carry_over_hours if eps else 0.0)

    entries = []
    if selected_employee:
        entries = (
            db.query(TimeEntry)
            .filter(TimeEntry.timesheet_id == active_ts_id, TimeEntry.employee_id == selected_employee.id)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
    grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)

    # Duplicate dates (exclude reviewed)
    dups_rows_raw = []
    dup_dates = set()
    dups = []
    if selected_employee:
        dups_rows_raw = (
            db.query(TimeEntry.work_date, func.count(TimeEntry.id))
            .filter(TimeEntry.timesheet_id == active_ts_id, TimeEntry.employee_id == selected_employee.id)
            .group_by(TimeEntry.work_date)
            .having(func.count(TimeEntry.id) > 1)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
        reviewed_dates = {
            r.work_date
            for r in db.query(DuplicateReview)
            .filter(DuplicateReview.timesheet_id == active_ts_id, DuplicateReview.employee_id == selected_employee.id)
            .all()
        }
        dups_rows = [(d, c) for d, c in dups_rows_raw if d not in reviewed_dates]
        dup_dates = {d for d, c in dups_rows}
        dups = [{"date": d, "count": int(c)} for d, c in dups_rows]

    # Long shift flags/reviews
    reviewed_long_dates = set()
    flagged_long_dates = set()
    if selected_employee:
        reviewed_long_dates = {
            r.work_date
            for r in db.query(LongShiftReview)
            .filter(LongShiftReview.timesheet_id == active_ts_id, LongShiftReview.employee_id == selected_employee.id)
            .all()
        }
        flagged_long_dates = {
            r.work_date
            for r in db.query(LongShiftFlag)
            .filter(LongShiftFlag.timesheet_id == active_ts_id, LongShiftFlag.employee_id == selected_employee.id)
            .all()
        }
        # Seed flags for any 10+ rows not yet flagged (so edits won't auto-clear)
        newly_flagged = 0
        for r in grouped.rows:
            if float(r.total_hours or 0.0) >= 10.0 and r.work_date not in flagged_long_dates:
                db.add(LongShiftFlag(timesheet_id=active_ts_id, employee_id=selected_employee.id, work_date=r.work_date))
                flagged_long_dates.add(r.work_date)
                newly_flagged += 1
        if newly_flagged:
            db.commit()

    # PTO needs: seed flags for any row with PTO hours and missing type
    reviewed_pto_dates = set()
    flagged_pto_dates = set()
    if selected_employee:
        reviewed_pto_dates = {
            r.work_date
            for r in db.query(PtoReviewFlag)
            .filter(PtoReviewFlag.timesheet_id == active_ts_id, PtoReviewFlag.employee_id == selected_employee.id)
            .all()
        }
        flagged_pto_dates = {
            r.work_date
            for r in db.query(PtoNeedFlag)
            .filter(PtoNeedFlag.timesheet_id == active_ts_id, PtoNeedFlag.employee_id == selected_employee.id)
            .all()
        }
        newly_flagged_pto = 0
        for r in grouped.rows:
            if float(r.pto_hours or 0.0) > 0.0 and not (r.pto_type or "").strip() and r.work_date not in flagged_pto_dates:
                db.add(PtoNeedFlag(timesheet_id=active_ts_id, employee_id=selected_employee.id, work_date=r.work_date))
                flagged_pto_dates.add(r.work_date)
                newly_flagged_pto += 1
        if newly_flagged_pto:
            db.commit()

    # Build notification lists based on flags + not-reviewed
    long_shift_needs = [r for r in grouped.rows if (r.work_date in flagged_long_dates and r.work_date not in reviewed_long_dates)]
    pto_needs = [r for r in grouped.rows if (r.work_date in flagged_pto_dates and r.work_date not in reviewed_pto_dates)]

    return templates.TemplateResponse(
        "viewer.html",
        {
            "request": request,
            "employees": employees,
            "selected_employee": selected_employee,
            "period_options": period_options,
            "active_ts": active_ts_id,
            "grouped": grouped,
            "employee_setting": {"carry_over_hours": carry},
            "duplicates": dups,
            "dup_dates": dup_dates,
            "pto_needs": pto_needs,
            "long_shift_needs": long_shift_needs,
            "reviewed_long_dates": reviewed_long_dates,
            "flagged_long_dates": flagged_long_dates,
            "reviewed_pto_dates": reviewed_pto_dates,
            "flagged_pto_dates": flagged_pto_dates,
            "flash": msg,
            "all_done": False,
            "can_edit": bool(request.session.get("is_admin")),
        },
    )

# -------- Review list --------
@app.get("/review", response_class=HTMLResponse)
@login_required
def review_page(
    request: Request,
    timesheet_id: Optional[int] = Query(None),
    msg: Optional[str] = None,
    db: Session = Depends(get_session),
):
    sheets = enumerate_timesheets_global(db)
    period_options = [{"timesheet_id": tid, "display": name} for tid, ps, pe, name in sheets]
    if not period_options:
        return templates.TemplateResponse(
            "review.html",
            {
                "request": request,
                "submitted": [],
                "active_ts": None,
                "period_options": [],
                "flash": "No submitted timesheets yet.",
            },
        )

    active_ts = timesheet_id or period_options[-1]["timesheet_id"]

    rows = (
        db.query(TimesheetStatus, Employee)
        .join(Employee, Employee.id == TimesheetStatus.employee_id)
        .filter(TimesheetStatus.timesheet_id == active_ts)
        .order_by(Employee.name.asc())
        .all()
    )
    submitted_rows = [
        {"employee_id": emp.id, "employee_name": emp.name, "submitted_at": tsrow.submitted_at}
        for tsrow, emp in rows
        if tsrow.status == "submitted"
    ]

    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "submitted": submitted_rows,
            "active_ts": active_ts,
            "period_options": period_options,
            "flash": msg,
        },
    )

# -------- Review edit --------
@app.get("/review/edit", response_class=HTMLResponse)
@login_required
def review_edit_page(
    request: Request,
    timesheet_id: int = Query(...),
    employee_id: int = Query(...),
    msg: Optional[str] = None,
    db: Session = Depends(get_session),
):
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    eps = (
        db.query(EmployeePeriodSetting)
        .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == emp.id)
        .first()
    )
    carry = float(eps.carry_over_hours if eps else 0.0)
    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}

    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )
    grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)

    # Duplicates (exclude reviewed)
    dups_rows_raw = (
        db.query(TimeEntry.work_date, func.count(TimeEntry.id))
        .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
        .group_by(TimeEntry.work_date)
        .having(func.count(TimeEntry.id) > 1)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )
    reviewed_dup_dates = {
        r.work_date
        for r in db.query(DuplicateReview)
        .filter(DuplicateReview.timesheet_id == timesheet_id, DuplicateReview.employee_id == emp.id)
        .all()
    }
    dups_rows = [(d, c) for d, c in dups_rows_raw if d not in reviewed_dup_dates]
    dup_dates = {d for d, c in dups_rows}
    duplicates = [{"date": d, "count": int(c)} for d, c in dups_rows]

    reviewed_long_dates = {
        r.work_date
        for r in db.query(LongShiftReview)
        .filter(LongShiftReview.timesheet_id == timesheet_id, LongShiftReview.employee_id == emp.id)
        .all()
    }
    flagged_long_dates = {
        r.work_date
        for r in db.query(LongShiftFlag)
        .filter(LongShiftFlag.timesheet_id == timesheet_id, LongShiftFlag.employee_id == emp.id)
        .all()
    }
    newly_flagged = 0
    for r in grouped.rows:
        if float(r.total_hours or 0.0) >= 10.0 and r.work_date not in flagged_long_dates:
            db.add(LongShiftFlag(timesheet_id=timesheet_id, employee_id=emp.id, work_date=r.work_date))
            flagged_long_dates.add(r.work_date)
            newly_flagged += 1
    if newly_flagged:
        db.commit()
    long_shift_needs = [r for r in grouped.rows if (r.work_date in flagged_long_dates and r.work_date not in reviewed_long_dates)]

    reviewed_pto_dates = {
        r.work_date
        for r in db.query(PtoReviewFlag)
        .filter(PtoReviewFlag.timesheet_id == timesheet_id, PtoReviewFlag.employee_id == emp.id)
        .all()
    }
    flagged_pto_dates = {
        r.work_date
        for r in db.query(PtoNeedFlag)
        .filter(PtoNeedFlag.timesheet_id == timesheet_id, PtoNeedFlag.employee_id == emp.id)
        .all()
    }
    newly_flagged_pto = 0
    for r in grouped.rows:
        if float(r.pto_hours or 0.0) > 0.0 and not (r.pto_type or "").strip() and r.work_date not in flagged_pto_dates:
            db.add(PtoNeedFlag(timesheet_id=timesheet_id, employee_id=emp.id, work_date=r.work_date))
            flagged_pto_dates.add(r.work_date)
            newly_flagged_pto += 1
    if newly_flagged_pto:
        db.commit()
    pto_needs = [r for r in grouped.rows if (r.work_date in flagged_pto_dates and r.work_date not in reviewed_pto_dates)]

    return templates.TemplateResponse(
        "review_edit.html",
        {
            "request": request,
            "employee": emp,
            "timesheet_id": timesheet_id,
            "period_name": ts.name or f"{ts.period_start}..{ts.period_end}",
            "grouped": grouped,
            "carry_over_hours": carry,
            "flash": msg,
            "duplicates": duplicates,
            "dup_dates": dup_dates,
            "long_shift_needs": long_shift_needs,
            "flagged_long_dates": flagged_long_dates,
            "reviewed_long_dates": reviewed_long_dates,
            "pto_needs": pto_needs,
            "flagged_pto_dates": flagged_pto_dates,
            "reviewed_pto_dates": reviewed_pto_dates,
            "can_edit": bool(request.session.get("is_admin")),
        },
    )

# -------- Print (single) --------
@app.get("/review/print", response_class=HTMLResponse)
@login_required
def review_print(
    request: Request,
    employee_id: int,
    timesheet_id: int,
    db: Session = Depends(get_session),
):
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    eps = (
        db.query(EmployeePeriodSetting)
        .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == emp.id)
        .first()
    )
    carry = float(eps.carry_over_hours if eps else 0.0)
    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}

    entries = (
        db.query(TimeEntry)
        .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )
    grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)

    return templates.TemplateResponse(
        "print_timesheet.html",
        {
            "request": request,
            "hide_nav_links": True,
            "employee": emp,
            "period_name": ts.name or f"{ts.period_start}..{ts.period_end}",
            "grouped": grouped,
            "timesheet_id": timesheet_id,
        },
    )

# -------- Print all --------
@app.get("/review/print-all", response_class=HTMLResponse)
@login_required
def review_print_all(
    request: Request,
    timesheet_id: int,
    db: Session = Depends(get_session),
):
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Time Period not found")

    rows = (
        db.query(TimesheetStatus, Employee)
        .join(Employee, Employee.id == TimesheetStatus.employee_id)
        .filter(TimesheetStatus.timesheet_id == timesheet_id, TimesheetStatus.status == "submitted")
        .order_by(Employee.name.asc())
        .all()
    )

    # Precompute week assignments once
    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}

    bundles = []
    for tsrow, emp in rows:
        eps = (
            db.query(EmployeePeriodSetting)
            .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == emp.id)
            .first()
        )
        carry = float(eps.carry_over_hours if eps else 0.0)
        entries = (
            db.query(TimeEntry)
            .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
        grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)
        bundles.append({"employee": emp, "grouped": grouped})

    return templates.TemplateResponse(
        "print_timesheet_bundle.html",
        {
            "request": request,
            "hide_nav_links": True,
            "timesheet_id": timesheet_id,
            "period_name": ts.name or f"{ts.period_start}..{ts.period_end}",
            "bundles": bundles,
        },
    )

# -------- Overview --------
@app.get("/overview", response_class=HTMLResponse)
@login_required
def overview_page(
    request: Request,
    timesheet_id: int = Query(...),
    db: Session = Depends(get_session),
):
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        return templates.TemplateResponse(
            "overview.html",
            {"request": request, "bundles": [], "period_name": "", "timesheet_id": timesheet_id, "totals": None},
        )

    # Week map for grouping
    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}

    # Employees who SUBMITTED this period
    employees = (
        db.query(Employee)
        .join(TimesheetStatus, TimesheetStatus.employee_id == Employee.id)
        .filter(TimesheetStatus.timesheet_id == timesheet_id, TimesheetStatus.status == "submitted")
        .order_by(Employee.name.asc())
        .all()
    )

    bundles = []
    sum_regular = Decimal("0")
    sum_pto = Decimal("0")
    sum_holiday = Decimal("0")
    sum_bereavement = Decimal("0")
    sum_ot = Decimal("0")
    sum_paid = Decimal("0")

    for emp in employees:
        entries = (
            db.query(TimeEntry)
            .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
        eps = (
            db.query(EmployeePeriodSetting)
            .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == emp.id)
            .first()
        )
        carry = float(eps.carry_over_hours if eps else 0.0)

        grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)
        t = grouped.totals
        sum_regular += Decimal(str(t.regular))
        sum_pto += Decimal(str(t.pto))
        sum_holiday += Decimal(str(t.holiday))
        sum_bereavement += Decimal(str(t.bereavement))
        sum_ot += Decimal(str(t.overtime))
        sum_paid += Decimal(str(t.paid_total))

        bundles.append({"employee": emp, "grouped": grouped})

    q = lambda d: float(Decimal(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    totals = {
        "regular": q(sum_regular),
        "pto": q(sum_pto),
        "holiday": q(sum_holiday),
        "bereavement": q(sum_bereavement),
        "overtime": q(sum_ot),
        "paid_total": q(sum_paid),
    }

    return templates.TemplateResponse(
        "overview.html",
        {
            "request": request,
            "bundles": bundles,
            "period_name": ts.name or f"{ts.period_start}..{ts.period_end}",
            "timesheet_id": timesheet_id,
            "totals": totals,
        },
    )

@app.get("/overview/print", response_class=HTMLResponse)
@login_required
def overview_print(
    request: Request,
    timesheet_id: int = Query(...),
    db: Session = Depends(get_session),
):
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        return templates.TemplateResponse(
            "print_overview.html",
            {"request": request, "bundles": [], "period_name": "", "timesheet_id": timesheet_id, "totals": None},
        )

    week_rows = db.query(WeekAssignment).filter(WeekAssignment.timesheet_id == timesheet_id).all()
    week_map = {wr.day_date: wr.week_number for wr in week_rows}

    employees = (
        db.query(Employee)
        .join(TimesheetStatus, TimesheetStatus.employee_id == Employee.id)
        .filter(TimesheetStatus.timesheet_id == timesheet_id, TimesheetStatus.status == "submitted")
        .order_by(Employee.name.asc())
        .all()
    )

    bundles = []
    sum_regular = Decimal("0")
    sum_pto = Decimal("0")
    sum_holiday = Decimal("0")
    sum_bereavement = Decimal("0")
    sum_ot = Decimal("0")
    sum_paid = Decimal("0")

    for emp in employees:
        entries = (
            db.query(TimeEntry)
            .filter(TimeEntry.timesheet_id == timesheet_id, TimeEntry.employee_id == emp.id)
            .order_by(TimeEntry.work_date.asc())
            .all()
        )
        eps = (
            db.query(EmployeePeriodSetting)
            .filter(EmployeePeriodSetting.timesheet_id == timesheet_id, EmployeePeriodSetting.employee_id == emp.id)
            .first()
        )
        carry = float(eps.carry_over_hours if eps else 0.0)
        grouped = group_entries_for_timesheet(entries, ts.period_start, ts.period_end, week_map=week_map, carry_over_hours=carry)

        t = grouped.totals
        sum_regular += Decimal(str(t.regular))
        sum_pto += Decimal(str(t.pto))
        sum_holiday += Decimal(str(t.holiday))
        sum_bereavement += Decimal(str(t.bereavement))
        sum_ot += Decimal(str(t.overtime))
        sum_paid += Decimal(str(t.paid_total))

        bundles.append({"employee": emp, "grouped": grouped})

    q = lambda d: float(Decimal(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    totals = {
        "regular": q(sum_regular),
        "pto": q(sum_pto),
        "holiday": q(sum_holiday),
        "bereavement": q(sum_bereavement),
        "overtime": q(sum_ot),
        "paid_total": q(sum_paid),
    }

    return templates.TemplateResponse(
        "print_overview.html",
        {
            "request": request,
            "hide_nav_links": True,
            "bundles": bundles,
            "period_name": ts.name or f"{ts.period_start}..{ts.period_end}",
            "timesheet_id": timesheet_id,
            "totals": totals,
        },
    )

# =======================
# PTO Tracker (Admin only, per-year)
# =======================
def _years_for_employee(db: Session, employee_id: int) -> List[int]:
    cur = datetime.utcnow().year
    years: Set[int] = set()
    # From submitted time entries
    rows = (
        db.query(func.extract("year", TimeEntry.work_date))
        .join(
            TimesheetStatus,
            (TimesheetStatus.timesheet_id == TimeEntry.timesheet_id)
            & (TimesheetStatus.employee_id == TimeEntry.employee_id),
        )
        .filter(TimeEntry.employee_id == employee_id, TimesheetStatus.status == "submitted")
        .distinct()
        .all()
    )
    years |= {int(float(r[0])) for r in rows if r[0] is not None}
    # From PTO accounts and adjustments
    years |= {
        y for (y,) in db.query(func.distinct(PTOAccount.year)).filter(PTOAccount.employee_id == employee_id).all() if y is not None
    }
    years |= {
        y for (y,) in db.query(func.distinct(PTOAdjustment.year)).filter(PTOAdjustment.employee_id == employee_id).all() if y is not None
    }
    # Always include current year and previous year
    years.add(cur)
    years.add(cur - 1)
    return sorted(years)

@app.get("/pto-tracker", response_class=HTMLResponse)
@login_required
def pto_tracker_page(
    request: Request,
    employee_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    include_inactive: int = Query(0),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Employees list (active-only by default)
    if include_inactive:
        employees_all = db.query(Employee).order_by(Employee.name.asc()).all()
    else:
        employees_all = (
            db.query(Employee)
            .filter(text("COALESCE(is_active, TRUE) = TRUE"))
            .order_by(Employee.name.asc())
            .all()
        )

    # For labeling inactive entries when showing all
    inactive_rows = db.execute(text("SELECT id FROM employees WHERE COALESCE(is_active, TRUE) = FALSE")).fetchall()
    inactive_ids = {r[0] for r in inactive_rows}

    if not employees_all:
        return templates.TemplateResponse(
            "pto_tracker.html",
            {
                "request": request,
                "employees": [],
                "selected_employee": None,
                "years": [datetime.utcnow().year],
                "selected_year": datetime.utcnow().year,
                "starting_balance": 0.0,
                "remaining_balance": 0.0,
                "ledger": [],
                "include_inactive": include_inactive,
                "inactive_ids": inactive_ids,
            },
        )

    selected = db.query(Employee).get(employee_id) if employee_id else employees_all[0]

    years = _years_for_employee(db, selected.id)
    sel_year = int(year) if year else (years[-1] if years else datetime.utcnow().year)
    if sel_year not in years:
        years.append(sel_year)
        years.sort()

    y_start = date(sel_year, 1, 1)
    y_end = date(sel_year, 12, 31)

    # Starting balance (per year)
    acct = (
        db.query(PTOAccount)
        .filter(PTOAccount.employee_id == selected.id, PTOAccount.year == sel_year)
        .first()
    )
    start_bal = D(acct.starting_balance if acct else 0)

    # Manual adjustments (per year)
    adjustments = (
        db.query(PTOAdjustment)
        .filter(PTOAdjustment.employee_id == selected.id, PTOAdjustment.year == sel_year)
        .order_by(PTOAdjustment.created_at.asc())
        .all()
    )

    # PTO usage from submitted timesheets only, filtered to selected year, excluding masked rows
    usage_rows = (
        db.query(TimeEntry.work_date, TimeEntry.pto_type, func.sum(TimeEntry.pto_hours).label("hours"))
        .join(
            TimesheetStatus,
            (TimesheetStatus.timesheet_id == TimeEntry.timesheet_id) & (TimesheetStatus.employee_id == TimeEntry.employee_id),
        )
        .outerjoin(
            PTOUsageExclusion,
            (PTOUsageExclusion.employee_id == selected.id)
            & (PTOUsageExclusion.work_date == TimeEntry.work_date)
            & (func.coalesce(PTOUsageExclusion.pto_type, "") == func.coalesce(TimeEntry.pto_type, "")),
        )
        .filter(
            TimeEntry.employee_id == selected.id,
            TimeEntry.pto_hours > 0,
            TimesheetStatus.status == "submitted",
            TimeEntry.work_date >= y_start,
            TimeEntry.work_date <= y_end,
            PTOUsageExclusion.id.is_(None),  # only include not-excluded
        )
        .group_by(TimeEntry.work_date, TimeEntry.pto_type)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )

    # Build ledger: starting, then adjustments and usage events with running balance
    events = []
    for a in adjustments:
        events.append({
            "kind": "adjustment",
            "date": a.created_at.date(),
            "desc": (a.note or "Adjustment"),
            "delta": D(a.hours),
            "adj_id": a.id,
        })
    for u_date, u_type, u_hours in usage_rows:
        events.append({
            "kind": "usage",
            "date": u_date,
            "desc": (u_type or "PTO"),
            "delta": -D(u_hours),
            "u_date": u_date.isoformat(),
            "u_type": u_type or "",
        })

    # Sort by date, then ensure adjustments come before usage on same date
    events.sort(key=lambda e: (e["date"], 0 if e["kind"] == "adjustment" else 1))

    running = q2(start_bal)
    ledger = []
    # Include a header row for starting balance (no delta)
    ledger.append({
        "date": None,
        "desc": f"Starting balance ({sel_year})",
        "delta": "",
        "balance": float(running),
        "kind": "start",
    })
    for ev in events:
        running = q2(running + ev["delta"])
        row = {
            "date": ev["date"],
            "desc": ev["desc"],
            "delta": float(q2(ev["delta"])),
            "balance": float(running),
            "kind": ev["kind"],
        }
        if ev["kind"] == "adjustment":
            row["adj_id"] = ev["adj_id"]
        else:
            row["u_date"] = ev["u_date"]
            row["u_type"] = ev["u_type"]
        ledger.append(row)

    remaining = float(running)

    return templates.TemplateResponse(
        "pto_tracker.html",
        {
            "request": request,
            "employees": employees_all,
            "selected_employee": selected,
            "years": years,
            "selected_year": sel_year,
            "starting_balance": float(q2(start_bal)),
            "remaining_balance": remaining,
            "ledger": ledger,
            "include_inactive": include_inactive,
            "inactive_ids": inactive_ids,
        },
    )

@app.post("/pto-tracker/set-starting")
@login_required
def pto_tracker_set_starting(
    request: Request,
    employee_id: int = Form(...),
    year: int = Form(...),
    starting_balance: str = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    bal = q2(D(starting_balance or "0"))
    acct = (
        db.query(PTOAccount)
        .filter(PTOAccount.employee_id == emp.id, PTOAccount.year == year)
        .first()
    )
    if not acct:
        acct = PTOAccount(employee_id=emp.id, year=year, starting_balance=bal, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add(acct)
    else:
        acct.starting_balance = bal
        acct.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/pto-tracker?employee_id={emp.id}&year={year}", status_code=303)

@app.post("/pto-tracker/add-adjustment")
@login_required
def pto_tracker_add_adjustment(
    request: Request,
    employee_id: int = Form(...),
    year: int = Form(...),
    hours: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)
    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    adj_hours = q2(D(hours or "0"))
    db.add(PTOAdjustment(employee_id=emp.id, year=year, hours=adj_hours, note=(note or "").strip(), created_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse(url=f"/pto-tracker?employee_id={emp.id}&year={year}", status_code=303)

@app.post("/pto-tracker/delete-adjustment")
@login_required
def pto_tracker_delete_adjustment(
    request: Request,
    employee_id: int = Form(...),
    year: int = Form(...),
    adjustment_id: int = Form(...),
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)
    adj = (
        db.query(PTOAdjustment)
        .filter(PTOAdjustment.id == adjustment_id, PTOAdjustment.employee_id == employee_id)
        .first()
    )
    if not adj:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    db.delete(adj)
    db.commit()
    return RedirectResponse(url=f"/pto-tracker?employee_id={employee_id}&year={year}", status_code=303)

@app.post("/pto-tracker/exclude-usage")
@login_required
def pto_tracker_exclude_usage(
    request: Request,
    employee_id: int = Form(...),
    year: int = Form(...),
    work_date: str = Form(...),  # ISO YYYY-MM-DD
    pto_type: str = Form(""),    # may be empty string for NULL
    db: Session = Depends(get_session),
):
    require_admin_edit(request, db)
    try:
        d = date.fromisoformat((work_date or "").strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date")

    t = (pto_type or "").strip() or None
    exists = (
        db.query(PTOUsageExclusion)
        .filter(
            PTOUsageExclusion.employee_id == employee_id,
            PTOUsageExclusion.work_date == d,
            func.coalesce(PTOUsageExclusion.pto_type, "") == func.coalesce(t, ""),
        )
        .first()
    )
    if not exists:
        db.add(PTOUsageExclusion(employee_id=employee_id, work_date=d, pto_type=t, created_at=datetime.utcnow()))
        db.commit()
    return RedirectResponse(url=f"/pto-tracker?employee_id={employee_id}&year={year}", status_code=303)

# -------- PTO Ledger builder (shared by print endpoints) --------
def _build_pto_ledger(db: Session, emp_id: int, sel_year: int):
    y_start = date(sel_year, 1, 1)
    y_end = date(sel_year, 12, 31)

    acct = db.query(PTOAccount).filter(PTOAccount.employee_id == emp_id, PTOAccount.year == sel_year).first()
    start_bal = D(acct.starting_balance if acct else 0)

    adjustments = (
        db.query(PTOAdjustment)
        .filter(PTOAdjustment.employee_id == emp_id, PTOAdjustment.year == sel_year)
        .order_by(PTOAdjustment.created_at.asc())
        .all()
    )

    usage_rows = (
        db.query(TimeEntry.work_date, TimeEntry.pto_type, func.sum(TimeEntry.pto_hours).label("hours"))
        .join(
            TimesheetStatus,
            (TimesheetStatus.timesheet_id == TimeEntry.timesheet_id) & (TimesheetStatus.employee_id == TimeEntry.employee_id),
        )
        .outerjoin(
            PTOUsageExclusion,
            (PTOUsageExclusion.employee_id == emp_id)
            & (PTOUsageExclusion.work_date == TimeEntry.work_date)
            & (func.coalesce(PTOUsageExclusion.pto_type, "") == func.coalesce(TimeEntry.pto_type, "")),
        )
        .filter(
            TimeEntry.employee_id == emp_id,
            TimeEntry.pto_hours > 0,
            TimesheetStatus.status == "submitted",
            TimeEntry.work_date >= y_start,
            TimeEntry.work_date <= y_end,
            PTOUsageExclusion.id.is_(None),
        )
        .group_by(TimeEntry.work_date, TimeEntry.pto_type)
        .order_by(TimeEntry.work_date.asc())
        .all()
    )

    events = []
    for a in adjustments:
        events.append({"kind": "adjustment", "date": a.created_at.date(), "desc": (a.note or "Adjustment"), "delta": D(a.hours)})
    for u_date, u_type, u_hours in usage_rows:
        events.append({"kind": "usage", "date": u_date, "desc": (u_type or "PTO"), "delta": -D(u_hours)})
    events.sort(key=lambda e: (e["date"], 0 if e["kind"] == "adjustment" else 1))

    running = q2(start_bal)
    ledger = [{"date": None, "desc": f"Starting balance ({sel_year})", "delta": "", "balance": float(running), "kind": "start"}]
    for ev in events:
        running = q2(running + ev["delta"])
        ledger.append({
            "date": ev["date"],
            "desc": ev["desc"],
            "delta": float(q2(ev["delta"])),
            "balance": float(running),
            "kind": ev["kind"],
        })
    return float(q2(start_bal)), float(running), ledger

# -------- PTO Tracker Print (single employee/year) --------
@app.get("/pto-tracker/print", response_class=HTMLResponse)
@login_required
def pto_tracker_print(
    request: Request,
    employee_id: int = Query(...),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_session),
):
    # PTO is admin-only in this app
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    emp = db.query(Employee).get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    years = _years_for_employee(db, emp.id)
    sel_year = int(year) if year else (years[-1] if years else datetime.utcnow().year)
    if sel_year not in years:
        years.append(sel_year)
        years.sort()

    start_bal, remaining, ledger = _build_pto_ledger(db, emp.id, sel_year)

    # Provide a timestamp for the print footer
    request.state.now = datetime.utcnow()

    return templates.TemplateResponse(
        "pto_tracker_print.html",
        {
            "request": request,
            "hide_nav_links": True,
            "employee": emp,
            "selected_year": sel_year,
            "starting_balance": start_bal,
            "remaining_balance": remaining,
            "ledger": ledger,
        },
    )

# -------- PTO Tracker Print All (all employees for a year) --------
@app.get("/pto-tracker/print-all", response_class=HTMLResponse)
@login_required
def pto_tracker_print_all(
    request: Request,
    year: Optional[int] = Query(None),
    include_inactive: int = Query(0),
    db: Session = Depends(get_session),
):
    if not current_is_admin(db, request.session.get("user_id")):
        raise HTTPException(status_code=403, detail="Admin access required")

    sel_year = int(year) if year else datetime.utcnow().year

    if include_inactive:
        employees = db.query(Employee).order_by(Employee.name.asc()).all()
    else:
        employees = (
            db.query(Employee)
            .filter(text("COALESCE(is_active, TRUE) = TRUE"))
            .order_by(Employee.name.asc())
            .all()
        )

    bundles = []
    for emp in employees:
        start_bal, remaining, ledger = _build_pto_ledger(db, emp.id, sel_year)
        bundles.append({
            "employee": emp,
            "starting_balance": start_bal,
            "remaining_balance": remaining,
            "ledger": ledger,
        })

    request.state.now = datetime.utcnow()
    return templates.TemplateResponse(
        "pto_tracker_print_all.html",
        {
            "request": request,
            "hide_nav_links": True,
            "selected_year": sel_year,
            "bundles": bundles,
        },
    )

# Mount the Attendance router
app.include_router(attendance_router)
app.include_router(dept_importer_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, log_level="info")
