"""Authentication utilities."""
from passlib.context import CryptContext
from functools import wraps
from fastapi import HTTPException, Request

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)

def verify_and_update_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    """Verify a password and return (verified, new_hash_if_needed)."""
    verified = pwd_context.verify(plain_password, hashed_password)
    # Check if rehashing is needed
    if verified and pwd_context.needs_update(hashed_password):
        return True, pwd_context.hash(plain_password)
    return verified, None

def login_required(func):
    """Decorator to require login."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("user_id"):
            raise HTTPException(status_code=401, detail="Not authenticated")
        return await func(request, *args, **kwargs)
    return wrapper
