"""Database connection and session management."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Use SQLite for development/testing if no DATABASE_URL is set
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./timekeeper.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_session():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ping_db():
    """Test database connectivity."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
