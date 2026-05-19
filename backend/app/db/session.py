from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def database_url() -> str | None:
    return (
        getattr(settings, "knowledge_database_url", None)
        or getattr(settings, "database_url", None)
        or getattr(settings, "trace_database_url", None)
    )


def sqlalchemy_database_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


def build_engine(url: str | None = None) -> Engine:
    resolved = url or database_url()
    if not resolved:
        raise ValueError("PostgreSQL DATABASE_URL is required")
    return create_engine(sqlalchemy_database_url(str(resolved)), pool_pre_ping=True)


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(url), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Generator[Session, None, None]:
    session_factory = get_session_factory(url)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
