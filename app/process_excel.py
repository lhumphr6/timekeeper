"""Excel import processing."""
from typing import Dict, Any


def import_workbook(file_path: str, db_session) -> Dict[str, Any]:
    """
    Import a workbook file.
    This is a stub implementation.
    """
    return {
        "success": False,
        "message": "Excel import not yet implemented",
        "rows_imported": 0,
    }
