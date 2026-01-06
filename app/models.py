"""Database models for Timekeeper application."""
from sqlalchemy import Column, Integer, String, Date, DateTime, Numeric, Boolean, ForeignKey, text
from sqlalchemy.orm import relationship
from datetime import datetime, date
from app.db import Base

class Employee(Base):
    """Employee model."""
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    termination_date = Column(Date, nullable=True)

class TimeEntry(Base):
    """Time entry model for employee clock in/out records."""
    __tablename__ = "time_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)
    clock_in = Column(DateTime, nullable=True)
    clock_out = Column(DateTime, nullable=True)
    total_hours = Column(Numeric(10, 2), default=0)
    break_hours = Column(Numeric(10, 2), default=0)
    pto_hours = Column(Numeric(10, 2), default=0)
    pto_type = Column(String(64), nullable=True)
    holiday_hours = Column(Numeric(10, 2), default=0)
    bereavement_hours = Column(Numeric(10, 2), default=0)
    hours_paid = Column(Numeric(10, 2), default=0)
    pto_clock_in_backup = Column(DateTime, nullable=True)
    pto_clock_out_backup = Column(DateTime, nullable=True)

class WeekAssignment(Base):
    """Week assignment for timesheet periods."""
    __tablename__ = "week_assignments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    day_date = Column(Date, nullable=False, index=True)
    week_number = Column(Integer, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

class TimesheetPeriod(Base):
    """Timesheet period model."""
    __tablename__ = "timesheet_periods"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

class User(Base):
    """User model for authentication."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

class TimesheetStatus(Base):
    """Timesheet submission status."""
    __tablename__ = "timesheet_status"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    status = Column(String(64), default="pending")
    submitted_at = Column(DateTime, default=datetime.utcnow)

class EmployeePeriodSetting(Base):
    """Employee settings for a specific period."""
    __tablename__ = "employee_period_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet_periods.id"), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    carry_over_hours = Column(Numeric(10, 2), default=0)

class DuplicateReview(Base):
    """Tracks reviewed duplicate entries."""
    __tablename__ = "duplicate_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timesheet_id = Column(Integer, nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    work_date = Column(Date, nullable=False, index=True)

class PTOAccount(Base):
    """PTO account balance."""
    __tablename__ = "pto_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    year = Column(Integer, nullable=True)
    starting_balance = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PTOAdjustment(Base):
    """PTO balance adjustments."""
    __tablename__ = "pto_adjustments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    year = Column(Integer, nullable=True)
    hours = Column(Numeric(10, 2), default=0)
    note = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
