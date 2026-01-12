# Import Another Department - Flow Diagram

## User Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                    TIMESHEET EDITOR (Viewer)                     │
│                                                                   │
│  [Time Period: January 2026 ▼]    [📥 Import Another Dept]      │
│                                    [↩️ Undo Last Import]         │
│                                                                   │
│  Employee: [John Doe ▼]                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Date       │ Clock In │ Clock Out │ Total │ Hours Paid   │  │
│  │ 01/06/2026 │ 08:00 AM │ 05:00 PM  │ 9.00  │ 8.50        │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────────────┘
                         │ Click "Import Another Dept"
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    IMPORT UPLOAD PAGE                            │
│                                                                   │
│  Time Period: [January 2026 ▼]                                  │
│                                                                   │
│  Upload File: [Choose File: dept-report.csv]                    │
│                                                                   │
│  [✓] Restrict to selected time period dates only                │
│                                                                   │
│  [Upload and Preview]                                            │
└────────────────────────┬────────────────────────────────────────┘
                         │ Upload CSV/XLSX
                         │ Parse & Normalize
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    IMPORT PREVIEW PAGE                           │
│                                                                   │
│  Time Period: January 2026                                       │
│  Source File: dept-report.csv                                    │
│  Employees Found: 4                                              │
│                                                                   │
│  [✓] Select/Deselect All                                         │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Import│Employee    │Status           │Rows │Date Range  │   │
│  ├───────┼────────────┼─────────────────┼─────┼────────────┤   │
│  │  [✓]  │John Doe    │New employee     │  5  │01/06-01/10 │   │
│  │  [✓]  │Jane Smith  │Existing in per. │  3  │01/06-01/08 │   │
│  │  [✓]  │Bob Johnson │Existing employee│  4  │01/06-01/09 │   │
│  │  [ ]  │Alice W.    │New employee     │  2  │01/06-01/07 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                   │
│  [Import Selected Employees]  [Cancel]                           │
└────────────────────────┬────────────────────────────────────────┘
                         │ Click "Import Selected"
                         │ Create ImportBatch
                         │ Create/Update Employees
                         │ Insert TimeEntries
                         │ Track ImportBatchItems
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SUCCESS MESSAGE                               │
│                                                                   │
│  ✅ Import complete: 12 entries imported, 2 skipped              │
│                                                                   │
│  Redirects back to Timesheet Editor                              │
└─────────────────────────────────────────────────────────────────┘


## Data Flow

CSV/XLSX File
     │
     ▼
┌─────────────────┐
│ Parse Headers   │  Normalize: "Employee" → "employee_name"
│ & Normalize     │            "In" → "clock_in"
└────────┬────────┘            "Date" → "date"
         │
         ▼
┌─────────────────┐
│ Parse Rows      │  Parse dates: "01/06/2026", "2026-01-06"
│ & Validate      │  Parse times: "08:00 AM", "17:00"
└────────┬────────┘  Combine time + date if needed
         │
         ▼
┌─────────────────┐
│ Normalize Data  │  Calculate total_hours from clock times
│ & Calculate     │  Validate numeric fields
└────────┬────────┘  Handle overnight shifts
         │
         ▼
┌─────────────────┐
│ Group by        │  Detect employee status:
│ Employee        │  - New (not in DB)
└────────┬────────┘  - Existing (in DB, not in period)
         │           - Existing in period (has entries)
         ▼
┌─────────────────┐
│ Store Temp JSON │  Save for preview → execute step
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Show Preview    │  User selects employees to import
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Execute Import  │  Create ImportBatch record
│                 │  For each selected employee:
│                 │    - Get/Create Employee
│                 │    - Check for duplicates
│                 │    - Validate week assignments
│                 │    - Create TimeEntry
│                 │    - Create ImportBatchItem
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Commit to DB    │  All-or-nothing transaction
└─────────────────┘


## Database Schema

┌──────────────────┐         ┌──────────────────┐
│  ImportBatch     │         │ TimesheetPeriod  │
├──────────────────┤         ├──────────────────┤
│ id (PK)          │         │ id (PK)          │
│ timesheet_id (FK)├────────▶│ name             │
│ source_name      │         │ period_start     │
│ created_at       │         │ period_end       │
└────────┬─────────┘         └──────────────────┘
         │
         │ 1:N
         ▼
┌──────────────────┐
│ ImportBatchItem  │         ┌──────────────────┐
├──────────────────┤         │ TimeEntry        │
│ id (PK)          │         ├──────────────────┤
│ batch_id (FK)    │         │ id (PK)          │
│ time_entry_id(FK)├────────▶│ employee_id (FK) │
└──────────────────┘         │ timesheet_id (FK)│
                             │ work_date        │
                             │ clock_in         │
                             │ clock_out        │
                             │ total_hours      │
                             │ break_hours      │
                             │ pto_hours        │
                             │ pto_type         │
                             │ hours_paid       │
                             └──────────────────┘


## Undo Flow

User clicks "Undo Last Import"
         │
         ▼
┌─────────────────────────────┐
│ Find most recent ImportBatch│
│ for current timesheet_id    │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Get all ImportBatchItems    │
│ for that batch              │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ For each item:              │
│  - Delete TimeEntry         │
│  - Delete ImportBatchItem   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Delete ImportBatch record   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│ Commit & Show success msg   │
└─────────────────────────────┘
```
