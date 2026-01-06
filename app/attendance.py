"""Attendance tracking router."""
from fastapi import APIRouter

router = APIRouter(prefix="/attendance", tags=["Attendance"])


@router.get("/")
def attendance_home():
    """Attendance home page."""
    return {"message": "Attendance tracking"}
