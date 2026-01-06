# Timekeeper

A FastAPI-based time tracking and timesheet management application.

## Features

- Employee time entry management
- Timesheet periods and week assignments
- PTO (Paid Time Off) tracking
- Admin user management
- **Import Another Department** - Import time entries from another department's CSV/XLSX file

## New Feature: Import Another Department

This feature allows administrators to import time entries from another department's time clock report.

### How to Use:

1. Navigate to the Timesheet Editor (`/viewer`)
2. Select the time period you want to import into
3. Click the "📥 Import Another Department" button
4. Upload a CSV or XLSX file with the following columns:
   - Employee Name
   - Date
   - Clock In
   - Clock Out
   - Break Hours (optional)
   - PTO Hours (optional)
   - PTO Type (optional)
5. Review the preview of employees to be imported
6. Select which employees to import
7. Click "Import Selected Employees"

The importer will:
- Create new employees if they don't exist
- Skip duplicate entries
- Only import entries for dates that exist in the timesheet's week assignments
- Handle overnight shifts (clock out < clock in treated as next day)

### Undo Last Import

If you need to undo an import:
1. Go to the Timesheet Editor for the period
2. Click the "↩️ Undo Last Import" button
3. Confirm the action

This will remove all entries from the most recent import batch for that timesheet period.

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python -m uvicorn app.main:app --host 0.0.0.0 --port 5070
```

## Configuration

Set environment variables:
- `DATABASE_URL` - Database connection string (defaults to SQLite: `sqlite:///./timekeeper.db`)
- `SECRET_KEY` - Session secret key
- `DEFAULT_ADMIN_USER` - Default admin username (default: "Admin")
- `DEFAULT_ADMIN_PASSWORD` - Default admin password (default: "1Senior!")
- `PORT` - Server port (default: 5070)

## Database Models

### Import Tracking Tables

- `import_batches` - Tracks each import operation
  - `id` - Batch ID
  - `timesheet_id` - Associated timesheet period
  - `source_name` - Original filename
  - `created_at` - Import timestamp

- `import_batch_items` - Links time entries to import batches
  - `id` - Item ID
  - `batch_id` - Associated batch
  - `time_entry_id` - Associated time entry

## API Endpoints

### Department Import Endpoints

- `GET /import/department` - Upload page
- `POST /import/department/upload` - Parse and preview file
- `POST /import/department/execute` - Execute import
- `POST /import/department/undo-last` - Undo last import

## Development

The application structure:
```
timekeeper/
├── app/
│   ├── __init__.py
│   ├── main.py              # Main application
│   ├── db.py                # Database setup
│   ├── models.py            # SQLAlchemy models
│   ├── auth.py              # Authentication
│   ├── utils.py             # Utility functions
│   ├── attendance.py        # Attendance tracking
│   ├── dept_importer.py     # Department import feature
│   ├── process_excel.py     # Excel processing
│   ├── templates/           # Jinja2 templates
│   └── static/              # Static assets
├── uploads/                 # Upload directory (auto-created)
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## License

Copyright © 2026 Senior Care Partners
