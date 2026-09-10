# backend/app/models/base.py
# Re-export for backwards compatibility with the previous flat layout.
# The single source of truth for the declarative Base now lives in
# app/core/database.py so every module shares one metadata registry.
from app.core.database import Base

__all__ = ["Base"]
