"""Database models."""
from sqlalchemy import Column, Integer, String, Date, DateTime, Float, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class User(Base):
    """User model for authentication."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Employee(Base):
    """Employee model."""
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TimesheetPeriod(Base):
    """Timesheet period model."""
    __tablename__ = "timesheet_periods"
    id = Column(Integer, primary_key=True, autoincrement=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TimeEntry(Base):
    """Time entry model."""
    __tablename__ = "time_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    clock_in = Column(DateTime, nullable=True)
    clock_out = Column(DateTime, nullable=True)
    break_hours = Column(Float, default=0)
    total_hours = Column(Float, default=0)
    pto_hours = Column(Float, default=0)
    pto_type = Column(String(50), nullable=True)
    holiday_hours = Column(Float, default=0)
    bereavement_hours = Column(Float, default=0)
    hours_paid = Column(Float, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WeekAssignment(Base):
    """Week assignment model for timesheet periods."""
    __tablename__ = "week_assignments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    day_date = Column(Date, nullable=False, index=True)
    week_number = Column(Integer, nullable=False)


class TimesheetStatus(Base):
    """Timesheet status model."""
    __tablename__ = "timesheet_statuses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    status = Column(String(50), default="draft")
    signed_at = Column(DateTime, nullable=True)
    reviewer_name = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)


class EmployeePeriodSetting(Base):
    """Employee settings for a specific period."""
    __tablename__ = "employee_period_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    status = Column(String(50), nullable=True)
    settings = Column(Text, nullable=True)


class DuplicateReview(Base):
    """Duplicate review tracking."""
    __tablename__ = "duplicate_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False)
    reviewed_at = Column(DateTime, default=datetime.utcnow)


class PTOAccount(Base):
    """PTO account model."""
    __tablename__ = "pto_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, unique=True, index=True)
    balance = Column(Float, default=0)
    year = Column(Integer, nullable=True)


class PTOAdjustment(Base):
    """PTO adjustment/transaction model."""
    __tablename__ = "pto_adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    adjustment_date = Column(Date, nullable=False)
    hours = Column(Float, nullable=False)
    reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
