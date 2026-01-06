"""Utility functions for TimeKeeper."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import List, Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func


def D(value) -> Decimal:
    """Convert to Decimal with proper rounding."""
    try:
        return Decimal(str(value))
    except:
        return Decimal("0.0")


def q2(value) -> str:
    """Quantize to 2 decimal places."""
    try:
        d = Decimal(str(value))
        return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except:
        return "0.00"


def enumerate_timesheets_global(db: Session) -> List[Tuple[int, date, date, str]]:
    """
    Enumerate all timesheet periods.
    Returns: List of (timesheet_id, period_start, period_end, period_name)
    """
    from .models import TimesheetPeriod
    
    periods = db.query(TimesheetPeriod).order_by(TimesheetPeriod.period_start.asc()).all()
    result = []
    for p in periods:
        name = p.period_name or f"{p.period_start.strftime('%Y-%m-%d')} to {p.period_end.strftime('%Y-%m-%d')}"
        result.append((p.id, p.period_start, p.period_end, name))
    return result


def group_entries_for_timesheet(db: Session, timesheet_id: int, employee_id: int) -> Dict[str, Any]:
    """
    Group time entries for a specific employee and timesheet.
    Returns a dictionary with weeks and entries.
    """
    from .models import TimeEntry, WeekAssignment
    
    # Get all entries for this employee and timesheet
    entries = db.query(TimeEntry).filter(
        TimeEntry.employee_id == employee_id,
        TimeEntry.timesheet_id == timesheet_id
    ).order_by(TimeEntry.work_date.asc()).all()
    
    # Get week assignments
    week_assignments = db.query(WeekAssignment).filter(
        WeekAssignment.timesheet_id == timesheet_id
    ).all()
    
    # Build week map
    week_map = {wa.day_date: wa.week_number for wa in week_assignments}
    
    # Group entries by week
    weeks = {}
    for entry in entries:
        week_num = week_map.get(entry.work_date, 1)
        if week_num not in weeks:
            weeks[week_num] = []
        weeks[week_num].append(entry)
    
    return {
        "weeks": weeks,
        "entries": entries,
        "week_map": week_map,
    }


def _semi_monthly_period_for_date(dt: date) -> Tuple[date, date]:
    """
    Calculate semi-monthly period for a given date.
    Returns (period_start, period_end)
    """
    if dt.day <= 15:
        # First half of month
        start = date(dt.year, dt.month, 1)
        end = date(dt.year, dt.month, 15)
    else:
        # Second half of month
        start = date(dt.year, dt.month, 16)
        # Find last day of month
        if dt.month == 12:
            end = date(dt.year, 12, 31)
        else:
            end = date(dt.year, dt.month + 1, 1) - timedelta(days=1)
    
    return start, end
