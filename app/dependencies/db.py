# backend/app/dependencies/db.py
from app.core.database import SessionLocal


def get_db():
    """Dependencia que provee una sesión de BD por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
