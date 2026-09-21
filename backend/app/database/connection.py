"""
database/connection.py — SQLModel engine + session management.

DATABASE_URL comes from the environment so the same code runs against
SQLite (fast local dev, no Docker required) and PostgreSQL (docker-compose /
production) unchanged:

    sqlite:///./storage/app.db                       (default, local dev)
    postgresql+psycopg2://user:pass@postgres:5432/db (docker-compose)
"""
import os
from contextlib import contextmanager

from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./storage/app.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def init_db():
    """Create tables if they don't exist. Import all models first so their
    metadata is registered on SQLModel.metadata before create_all runs."""
    from app.models import email, document, shipment, discrepancy, review  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency: yields a Session per-request."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope():
    """Use outside of request handlers (background tasks, scripts)."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
