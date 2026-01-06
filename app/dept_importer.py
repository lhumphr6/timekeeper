"""Department importer module for importing time entries from another department."""
import os
import json
import csv
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from decimal import Decimal

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import Employee, TimeEntry, WeekAssignment, TimesheetPeriod
from app.utils import D, q2

router = APIRouter(prefix="/import", tags=["import"])

# Import batch tracking models for undo functionality
class ImportBatch(Base):
    """Tracks import batches for undo."""
    __tablename__ = "import_batches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    source_name = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class ImportBatchItem(Base):
    """Tracks individual entries in an import batch."""
    __tablename__ = "import_batch_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("import_batches.id"), nullable=False, index=True)
    time_entry_id = Column(Integer, ForeignKey("time_entries.id"), nullable=False, index=True)


def _normalize_header(h: str) -> str:
    """Normalize header names to standard field names."""
    h = h.lower().strip().replace(" ", "_").replace("-", "_")
    # Common variants
    header_mapping = {
        "employee": "employee_name",
        "name": "employee_name",
        "emp_name": "employee_name",
        "worker": "employee_name",
        "date": "date",
        "work_date": "date",
        "day": "date",
        "in": "clock_in",
        "clock_in": "clock_in",
        "time_in": "clock_in",
        "start": "clock_in",
        "start_time": "clock_in",
        "out": "clock_out",
        "clock_out": "clock_out",
        "time_out": "clock_out",
        "end": "clock_out",
        "end_time": "clock_out",
        "break": "break_hours",
        "break_hours": "break_hours",
        "break_time": "break_hours",
        "pto": "pto_hours",
        "pto_hours": "pto_hours",
        "pto_time": "pto_hours",
        "paid_time_off": "pto_hours",
        "pto_type": "pto_type",
        "paid_time_off_type": "pto_type",
        "leave_type": "pto_type",
    }
    return header_mapping.get(h, h)


def _parse_datetime(val: Any) -> Optional[datetime]:
    """Parse datetime from various formats."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    
    s = str(val).strip()
    if not s:
        return None
    
    # Try various formats
    formats = [
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_date(val: Any) -> Optional[date]:
    """Parse date from various formats."""
    dt = _parse_datetime(val)
    return dt.date() if dt else None


def _parse_decimal(val: Any) -> Decimal:
    """Parse decimal value."""
    if val is None or val == "":
        return Decimal("0")
    try:
        return q2(D(val))
    except Exception:
        return Decimal("0")


def _parse_csv_or_xlsx(file_path: str) -> List[Dict[str, Any]]:
    """Parse CSV or XLSX file and return normalized rows."""
    rows = []
    
    # Check file extension
    if file_path.lower().endswith(('.xlsx', '.xlsm', '.xls')):
        # Parse Excel file
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)
            ws = wb.active
            
            # Get headers from first row
            headers = []
            for cell in ws[1]:
                if cell.value:
                    headers.append(_normalize_header(str(cell.value)))
                else:
                    headers.append(f"col_{len(headers)}")
            
            # Parse data rows
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not any(row):  # Skip empty rows
                    continue
                row_dict = {}
                for i, val in enumerate(row):
                    if i < len(headers):
                        row_dict[headers[i]] = val
                rows.append(row_dict)
        except ImportError:
            # Fall back to CSV if openpyxl not available
            raise HTTPException(status_code=400, detail="Excel support not available. Please use CSV format.")
    else:
        # Parse CSV file
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            headers = [_normalize_header(h) for h in reader.fieldnames]
            for row in reader:
                # Remap keys with normalized headers
                normalized_row = {}
                for old_key, new_key in zip(reader.fieldnames, headers):
                    normalized_row[new_key] = row[old_key]
                rows.append(normalized_row)
    
    return rows


def _normalize_row(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a single row to standard format."""
    normalized = {
        "employee_name": str(row_dict.get("employee_name", "")).strip(),
        "date": _parse_date(row_dict.get("date")),
        "clock_in": _parse_datetime(row_dict.get("clock_in")),
        "clock_out": _parse_datetime(row_dict.get("clock_out")),
        "break_hours": _parse_decimal(row_dict.get("break_hours")),
        "pto_hours": _parse_decimal(row_dict.get("pto_hours")),
        "pto_type": str(row_dict.get("pto_type", "")).strip() or None,
    }
    
    # Handle overnight clock out (clock_out <= clock_in means next day)
    if normalized["clock_in"] and normalized["clock_out"]:
        if normalized["clock_out"] <= normalized["clock_in"]:
            normalized["clock_out"] += timedelta(days=1)
    
    # Calculate total_hours if clock times are present
    if normalized["clock_in"] and normalized["clock_out"]:
        delta = normalized["clock_out"] - normalized["clock_in"]
        normalized["total_hours"] = q2(D(delta.total_seconds()) / D(3600))
    else:
        normalized["total_hours"] = Decimal("0")
    
    return normalized


@router.get("/department", response_class=HTMLResponse)
async def import_department_page(
    request: Request,
    timesheet_id: Optional[int] = Query(None),
):
    """Upload page for importing another department's data."""
    # Check admin access
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Get templates from app state
    templates = request.app.state.templates
    
    # Get available timesheets
    from app.db import SessionLocal
    with SessionLocal() as db:
        from app.utils import enumerate_timesheets_global
        sheets = enumerate_timesheets_global(db)
        period_options = [
            {"timesheet_id": tid, "display": name}
            for tid, ps, pe, name in sheets
        ]
    
    return templates.TemplateResponse(
        "dept_importer_upload.html",
        {
            "request": request,
            "period_options": period_options,
            "selected_timesheet_id": timesheet_id,
        }
    )


@router.post("/department/upload")
async def import_department_upload(
    request: Request,
    file: UploadFile = File(...),
    timesheet_id: int = Form(...),
    restrict_to_period: bool = Form(False),
):
    """Parse uploaded file and show preview."""
    # Check admin access
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    templates = request.app.state.templates
    
    # Validate file type
    allowed_ext = ('.csv', '.xlsx', '.xlsm', '.xls')
    if not file.filename.lower().endswith(allowed_ext):
        from app.utils import enumerate_timesheets_global
        from app.db import SessionLocal
        with SessionLocal() as db:
            sheets = enumerate_timesheets_global(db)
            period_options = [{"timesheet_id": tid, "display": name} for tid, ps, pe, name in sheets]
        return templates.TemplateResponse(
            "dept_importer_upload.html",
            {
                "request": request,
                "period_options": period_options,
                "selected_timesheet_id": timesheet_id,
                "error": f"Unsupported file type. Please upload: {', '.join(allowed_ext)}"
            },
            status_code=400
        )
    
    # Save uploaded file
    os.makedirs("uploads", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    saved_path = os.path.join("uploads", f"dept-import-{timestamp}-{file.filename}")
    contents = await file.read()
    with open(saved_path, "wb") as f:
        f.write(contents)
    
    try:
        # Parse file
        raw_rows = _parse_csv_or_xlsx(saved_path)
        
        # Normalize rows
        normalized_rows = []
        for row in raw_rows:
            try:
                norm = _normalize_row(row)
                if norm["employee_name"] and norm["date"]:  # Must have employee and date
                    normalized_rows.append(norm)
            except Exception as e:
                print(f"[dept_importer] Error normalizing row: {e}")
                continue
        
        if not normalized_rows:
            from app.utils import enumerate_timesheets_global
            from app.db import SessionLocal
            with SessionLocal() as db:
                sheets = enumerate_timesheets_global(db)
                period_options = [{"timesheet_id": tid, "display": name} for tid, ps, pe, name in sheets]
            return templates.TemplateResponse(
                "dept_importer_upload.html",
                {
                    "request": request,
                    "period_options": period_options,
                    "selected_timesheet_id": timesheet_id,
                    "error": "No valid rows found in the file."
                },
                status_code=400
            )
        
        # Restrict to period if requested
        from app.db import SessionLocal
        with SessionLocal() as db:
            ts = db.query(TimesheetPeriod).get(timesheet_id)
            if not ts:
                raise HTTPException(status_code=404, detail="Timesheet period not found")
            
            if restrict_to_period:
                normalized_rows = [
                    r for r in normalized_rows
                    if ts.period_start <= r["date"] <= ts.period_end
                ]
            
            # Group by employee
            employee_groups = {}
            for row in normalized_rows:
                emp_name = row["employee_name"]
                if emp_name not in employee_groups:
                    employee_groups[emp_name] = []
                employee_groups[emp_name].append(row)
            
            # Build preview data
            preview_employees = []
            for emp_name, emp_rows in employee_groups.items():
                # Check if employee exists
                emp = db.query(Employee).filter(Employee.name == emp_name).first()
                
                if emp:
                    # Check if employee has entries in this period
                    existing_count = db.query(TimeEntry).filter(
                        TimeEntry.employee_id == emp.id,
                        TimeEntry.timesheet_id == timesheet_id
                    ).count()
                    
                    if existing_count > 0:
                        status = f"Existing in period ({existing_count} entries)"
                    else:
                        status = "Existing employee"
                else:
                    status = "New employee"
                
                preview_employees.append({
                    "name": emp_name,
                    "status": status,
                    "row_count": len(emp_rows),
                    "date_range": f"{min(r['date'] for r in emp_rows)} to {max(r['date'] for r in emp_rows)}"
                })
            
            # Save normalized data to temporary JSON file for next step
            json_path = os.path.join("uploads", f"dept-import-{timestamp}.json")
            with open(json_path, "w") as f:
                # Convert dates/datetimes to ISO format for JSON
                serializable_rows = []
                for row in normalized_rows:
                    ser_row = row.copy()
                    if ser_row["date"]:
                        ser_row["date"] = ser_row["date"].isoformat()
                    if ser_row["clock_in"]:
                        ser_row["clock_in"] = ser_row["clock_in"].isoformat()
                    if ser_row["clock_out"]:
                        ser_row["clock_out"] = ser_row["clock_out"].isoformat()
                    ser_row["total_hours"] = str(ser_row["total_hours"])
                    ser_row["break_hours"] = str(ser_row["break_hours"])
                    ser_row["pto_hours"] = str(ser_row["pto_hours"])
                    serializable_rows.append(ser_row)
                
                json.dump({
                    "timesheet_id": timesheet_id,
                    "source_name": file.filename,
                    "rows": serializable_rows
                }, f)
            
            return templates.TemplateResponse(
                "dept_importer_preview.html",
                {
                    "request": request,
                    "timesheet_id": timesheet_id,
                    "timesheet_name": ts.name or f"{ts.period_start} to {ts.period_end}",
                    "source_file": file.filename,
                    "preview_employees": preview_employees,
                    "json_path": json_path,
                }
            )
    
    except Exception as e:
        print(f"[dept_importer] Error processing upload: {e}")
        from app.utils import enumerate_timesheets_global
        from app.db import SessionLocal
        with SessionLocal() as db:
            sheets = enumerate_timesheets_global(db)
            period_options = [{"timesheet_id": tid, "display": name} for tid, ps, pe, name in sheets]
        return templates.TemplateResponse(
            "dept_importer_upload.html",
            {
                "request": request,
                "period_options": period_options,
                "selected_timesheet_id": timesheet_id,
                "error": f"Error processing file: {e}"
            },
            status_code=500
        )


@router.post("/department/execute")
async def import_department_execute(
    request: Request,
    json_path: str = Form(...),
    timesheet_id: int = Form(...),
    selected_employees: List[str] = Form([]),
    db: Session = Depends(get_session),
):
    """Execute the import for selected employees."""
    # Check admin access
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Load normalized data from JSON
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Import data not found")
    
    with open(json_path, "r") as f:
        import_data = json.load(f)
    
    # Verify timesheet_id matches
    if import_data["timesheet_id"] != timesheet_id:
        raise HTTPException(status_code=400, detail="Timesheet ID mismatch")
    
    # Get timesheet period
    ts = db.query(TimesheetPeriod).get(timesheet_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Timesheet period not found")
    
    # Get week assignments for validation
    week_assignments = db.query(WeekAssignment).filter(
        WeekAssignment.timesheet_id == timesheet_id
    ).all()
    valid_dates = {wa.day_date for wa in week_assignments}
    
    # Parse selected employees from form
    if not selected_employees or selected_employees == ['']:
        selected_employees = []
    
    # Create import batch
    batch = ImportBatch(
        timesheet_id=timesheet_id,
        source_name=import_data["source_name"],
        created_at=datetime.utcnow()
    )
    db.add(batch)
    db.flush()
    
    imported_count = 0
    skipped_count = 0
    
    # Process rows
    for row_data in import_data["rows"]:
        emp_name = row_data["employee_name"]
        
        # Skip if employee not selected
        if selected_employees and emp_name not in selected_employees:
            continue
        
        # Deserialize dates/times
        work_date = date.fromisoformat(row_data["date"])
        clock_in = datetime.fromisoformat(row_data["clock_in"]) if row_data["clock_in"] else None
        clock_out = datetime.fromisoformat(row_data["clock_out"]) if row_data["clock_out"] else None
        total_hours = D(row_data["total_hours"])
        break_hours = D(row_data["break_hours"])
        pto_hours = D(row_data["pto_hours"])
        pto_type = row_data["pto_type"]
        
        # Skip if date not in week assignments
        if work_date not in valid_dates:
            skipped_count += 1
            continue
        
        # Get or create employee
        emp = db.query(Employee).filter(Employee.name == emp_name).first()
        if not emp:
            emp = Employee(name=emp_name, is_active=True)
            db.add(emp)
            db.flush()
        
        # Check for duplicate
        existing = db.query(TimeEntry).filter(
            TimeEntry.employee_id == emp.id,
            TimeEntry.timesheet_id == timesheet_id,
            TimeEntry.work_date == work_date,
            TimeEntry.clock_in == clock_in,
            TimeEntry.clock_out == clock_out
        ).first()
        
        if existing:
            skipped_count += 1
            continue
        
        # Calculate hours_paid
        worked = total_hours - break_hours
        if worked < 0:
            worked = Decimal("0")
        hours_paid = q2(worked + pto_hours)
        
        # Create time entry
        entry = TimeEntry(
            employee_id=emp.id,
            timesheet_id=timesheet_id,
            work_date=work_date,
            clock_in=clock_in,
            clock_out=clock_out,
            total_hours=total_hours,
            break_hours=break_hours,
            pto_hours=pto_hours,
            pto_type=pto_type,
            holiday_hours=Decimal("0"),
            bereavement_hours=Decimal("0"),
            hours_paid=hours_paid
        )
        db.add(entry)
        db.flush()
        
        # Track in batch
        db.add(ImportBatchItem(batch_id=batch.id, time_entry_id=entry.id))
        imported_count += 1
    
    db.commit()
    
    # Clean up JSON file
    try:
        os.remove(json_path)
    except Exception:
        pass
    
    msg = f"Import complete: {imported_count} entries imported, {skipped_count} skipped"
    return RedirectResponse(
        url=f"/viewer?timesheet_id={timesheet_id}&msg={msg}",
        status_code=303
    )


@router.post("/department/undo-last")
async def import_department_undo_last(
    request: Request,
    timesheet_id: int = Form(...),
    db: Session = Depends(get_session),
):
    """Undo the most recent department import for this timesheet."""
    # Check admin access
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Find the most recent batch for this timesheet
    batch = db.query(ImportBatch).filter(
        ImportBatch.timesheet_id == timesheet_id
    ).order_by(ImportBatch.created_at.desc()).first()
    
    if not batch:
        msg = "No import batch found to undo"
        return RedirectResponse(
            url=f"/viewer?timesheet_id={timesheet_id}&msg={msg}",
            status_code=303
        )
    
    # Get all entries in this batch
    batch_items = db.query(ImportBatchItem).filter(
        ImportBatchItem.batch_id == batch.id
    ).all()
    
    # Delete the time entries
    deleted_count = 0
    for item in batch_items:
        entry = db.query(TimeEntry).get(item.time_entry_id)
        if entry:
            db.delete(entry)
            deleted_count += 1
        db.delete(item)
    
    # Delete the batch record
    db.delete(batch)
    db.commit()
    
    msg = f"Undo complete: {deleted_count} entries removed from import batch"
    return RedirectResponse(
        url=f"/viewer?timesheet_id={timesheet_id}&msg={msg}",
        status_code=303
    )
