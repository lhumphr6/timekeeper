"""SQLAlchemy models for TimeKeeper."""
from sqlalchemy import Column, Integer, String, Date, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class User(Base):
    """User authentication table."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Employee(Base):
    """Employee information."""
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    active = Column(Integer, default=1)  # 1 = active, 0 = inactive


class TimesheetPeriod(Base):
    """Timesheet periods/pay periods."""
    __tablename__ = "timesheet_periods"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WeekAssignment(Base):
    """Week assignments for each day in a timesheet period."""
    __tablename__ = "week_assignments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    day_date = Column(Date, nullable=False, index=True)
    week_number = Column(Integer, nullable=False)


class TimeEntry(Base):
    """Individual time entries for employees."""
    __tablename__ = "time_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    clock_in = Column(DateTime, nullable=True)
    clock_out = Column(DateTime, nullable=True)
    break_hours = Column(Float, default=0.0)
    total_hours = Column(Float, default=0.0)
    pto_hours = Column(Float, default=0.0)
    pto_type = Column(String(50), nullable=True)
    holiday_hours = Column(Float, default=0.0)
    bereavement_hours = Column(Float, default=0.0)
    hours_paid = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)


class TimesheetStatus(Base):
    """Status tracking for timesheets."""
    __tablename__ = "timesheet_statuses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    status = Column(String(50), nullable=True)  # e.g., 'submitted', 'approved'
    submitted_at = Column(DateTime, nullable=True)


class EmployeePeriodSetting(Base):
    """Employee-specific settings for a timesheet period."""
    __tablename__ = "employee_period_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    carry_over_hours = Column(Float, default=0.0)


class DuplicateReview(Base):
    """Track reviewed duplicate entries."""
    __tablename__ = "duplicate_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    reviewed_at = Column(DateTime, default=datetime.utcnow)


class PTOAccount(Base):
    """PTO account balances for employees."""
    __tablename__ = "pto_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True, unique=True)
    balance_hours = Column(Float, default=0.0)
    last_updated = Column(DateTime, default=datetime.utcnow)


class PTOAdjustment(Base):
    """Manual PTO adjustments."""
    __tablename__ = "pto_adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    adjustment_hours = Column(Float, nullable=False)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(255), nullable=True)
