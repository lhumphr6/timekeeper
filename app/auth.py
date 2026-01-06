"""Authentication utilities."""
from functools import wraps
from fastapi import HTTPException, Request
from typing import Callable
import hashlib
import secrets


def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return hash_password(password) == hashed


def verify_and_update_password(password: str, hashed: str) -> bool:
    """Verify password (compatibility function)."""
    return verify_password(password, hashed)


def login_required(func: Callable):
    """Decorator to require login for a route."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user_id = request.session.get("user_id")
        if not user_id:
            raise HTTPException(status_code=403, detail="Login required")
        return await func(request, *args, **kwargs)
    return wrapper
