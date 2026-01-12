"""Utility functions."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import List, Optional
from dataclasses import dataclass

def D(value) -> Decimal:
    """Convert to Decimal."""
    return Decimal(str(value))

def q2(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

@dataclass
class TimesheetRow:
    """Represents a single row in a timesheet."""
    work_date: date
    clock_in: Optional[str]
    clock_out: Optional[str]
    total_hours: Decimal
    break_hours: Decimal
    pto_hours: Decimal
    pto_type: Optional[str]
    holiday_hours: Decimal
    bereavement_hours: Decimal
    hours_paid: Decimal
    entry_id: int

@dataclass
class TimesheetTotals:
    """Totals for a timesheet."""
    regular: float
    overtime: float
    pto: float
    holiday: float
    bereavement: float
    paid_total: float

@dataclass
class GroupedTimesheet:
    """Grouped timesheet data."""
    rows: List[TimesheetRow]
    totals: TimesheetTotals
    week_totals: dict

def group_entries_for_timesheet(entries, period_start, period_end, week_map=None, carry_over_hours=0):
    """Group time entries for display."""
    rows = []
    for entry in entries:
        rows.append(TimesheetRow(
            work_date=entry.work_date,
            clock_in=entry.clock_in,
            clock_out=entry.clock_out,
            total_hours=D(entry.total_hours or 0),
            break_hours=D(entry.break_hours or 0),
            pto_hours=D(entry.pto_hours or 0),
            pto_type=entry.pto_type,
            holiday_hours=D(entry.holiday_hours or 0),
            bereavement_hours=D(entry.bereavement_hours or 0),
            hours_paid=D(entry.hours_paid or 0),
            entry_id=entry.id
        ))
    
    # Calculate totals (simplified)
    total_hours = sum(r.hours_paid for r in rows)
    regular = min(80, float(total_hours))
    overtime = max(0, float(total_hours) - 80)
    pto = sum(float(r.pto_hours) for r in rows)
    holiday = sum(float(r.holiday_hours) for r in rows)
    bereavement = sum(float(r.bereavement_hours) for r in rows)
    
    totals = TimesheetTotals(
        regular=regular,
        overtime=overtime,
        pto=pto,
        holiday=holiday,
        bereavement=bereavement,
        paid_total=float(total_hours)
    )
    
    return GroupedTimesheet(rows=rows, totals=totals, week_totals={})

def enumerate_timesheets_global(db):
    """List all timesheet periods."""
    from app.models import TimesheetPeriod
    periods = db.query(TimesheetPeriod).order_by(TimesheetPeriod.period_start.asc()).all()
    return [(p.id, p.period_start, p.period_end, p.name or f"{p.period_start} to {p.period_end}") for p in periods]

def _semi_monthly_period_for_date(d: date) -> tuple[date, date]:
    """Calculate semi-monthly period for a date."""
    if d.day <= 15:
        return date(d.year, d.month, 1), date(d.year, d.month, 15)
    else:
        # Last day of month
        next_month = d.month + 1 if d.month < 12 else 1
        next_year = d.year if d.month < 12 else d.year + 1
        last_day = date(next_year, next_month, 1) - timedelta(days=1)
        return date(d.year, d.month, 16), last_day
