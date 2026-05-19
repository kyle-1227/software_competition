from __future__ import annotations

from typing import Any

from app.db.session import database_url


class PsycopgRepository:
    def __init__(self, database_url_value: str | None = None) -> None:
        self.database_url = database_url_value or database_url()
        if not self.database_url:
            raise ValueError("PostgreSQL DATABASE_URL is required")

    def _connect(self, *, autocommit: bool = True):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - dependency guard.
            raise RuntimeError("psycopg is required for PostgreSQL repositories") from exc
        return psycopg.connect(str(self.database_url), autocommit=autocommit)


def jsonb(value: dict[str, Any]):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - dependency guard.
        raise RuntimeError("psycopg is required for PostgreSQL repositories") from exc
    return Jsonb(value)
