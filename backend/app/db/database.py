"""SQLAlchemy engine and session setup."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for persisted API resources."""


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create the configured SQLAlchemy engine."""
    database_url = (settings or get_settings()).database_url
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create non-expiring sessions bound to one engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
