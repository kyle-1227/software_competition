from __future__ import annotations

from app.db.session import (
    build_engine,
    database_url,
    sqlalchemy_database_url,
)

knowledge_database_url = database_url
sqlalchemy_knowledge_database_url = sqlalchemy_database_url


def build_knowledge_engine(database_url: str | None = None):
    return build_engine(database_url)
