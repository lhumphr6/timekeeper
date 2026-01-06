"""Utility functions."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, timedelta
from typing import List, Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

def D(value) -> Decimal:
    """Convert to Decimal with rounding."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except:
        return Decimal("0.00")


def q2(value) -> Decimal:
    """Quantize to 2 decimal places."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except:
        return Decimal("0.00")


def enumerate_timesheets_global(db: Session) -> List[Tuple[int, date, date, Optional[str]]]:
    """
    Enumerate all timesheet periods.
    Returns list of tuples: (timesheet_id, period_start, period_end, name)
    """
    from .models import TimesheetPeriod
    
    periods = db.query(TimesheetPeriod).order_by(TimesheetPeriod.period_start.asc()).all()
    return [
        (p.id, p.period_start, p.period_end, p.name or f"{p.period_start} to {p.period_end}")
        for p in periods
    ]


def group_entries_for_timesheet(db: Session, timesheet_id: int) -> Dict[int, List[Any]]:
    """Group time entries by employee for a given timesheet."""
    from .models import TimeEntry, Employee
    
    entries = db.query(TimeEntry).filter(TimeEntry.timesheet_id == timesheet_id).all()
    grouped = {}
    for entry in entries:
        if entry.employee_id not in grouped:
            grouped[entry.employee_id] = []
        grouped[entry.employee_id].append(entry)
    return grouped


def _semi_monthly_period_for_date(d: date) -> Tuple[date, date]:
    """
    Calculate semi-monthly period for a given date.
    Returns (period_start, period_end) tuple.
    """
    if d.day <= 15:
        # First half of month: 1st to 15th
        start = date(d.year, d.month, 1)
        end = date(d.year, d.month, 15)
    else:
        # Second half of month: 16th to end of month
        start = date(d.year, d.month, 16)
        # Calculate last day of month
        if d.month == 12:
            end = date(d.year, 12, 31)
        else:
            end = date(d.year, d.month + 1, 1) - timedelta(days=1)
    return (start, end)
