"""
Aptiro backend — db/engine.py (Phase 9 PR-5).

SQLAlchemy/SQLModel engine, session dependency, and the UTC timestamp
factory used throughout the application as a model default_factory.

Extracted from backend/app/legacy.py.

Depends only on:
  app.core.config  — DATABASE_URL
  sqlmodel         — create_engine, Session
  stdlib           — datetime, sys

Public names (re-exported by legacy.py):
    _now()      → datetime  UTC timestamp (model Field default_factory)
    _IS_SQLITE  bool        True when database is SQLite
    engine      Engine      the live SQLAlchemy engine
    get_session() Generator FastAPI dependency — yields a Session

NOT here (still in legacy.py — see script docstring for why):
    _ensure_additive_columns, _backfill_owner_ids, _ensure_default_user,
    init_db, _mw_session
"""
import sys as _sys
from datetime import datetime, timezone

from sqlmodel import Session, create_engine

from app.core.config import DATABASE_URL  # noqa: F401


def _now() -> datetime:
    """UTC timestamp — used as default_factory in every SQLModel Field
    that records a creation or update time."""
    return datetime.now(timezone.utc)


_IS_SQLITE: bool = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _IS_SQLITE else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def get_session():
    """FastAPI dependency — yields an open SQLModel Session.

    Tests override this via app.dependency_overrides so they get an
    in-memory SQLite session instead of the real database.
    """
    with Session(engine) as s:
        yield s
