"""Authentication utilities."""
import hashlib
from functools import wraps
from fastapi import HTTPException, Request


def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_and_update_password(stored_hash: str, provided_password: str) -> bool:
    """Verify a password against its hash."""
    return stored_hash == hash_password(provided_password)


def login_required(func):
    """Decorator to require login for a route."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("user_id"):
            raise HTTPException(status_code=403, detail="Login required")
        return await func(request, *args, **kwargs)
    return wrapper
