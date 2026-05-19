from __future__ import annotations

import pytest

from app.core.config import settings
from app.db.models import Base
from app.db.session import sqlalchemy_database_url
from app.knowledge.document_repository import DocumentRepository
from app.knowledge.evidence_ledger import EvidenceLedgerRepository
from app.schemas.query import EvidenceItem
from app.services.knowledge.migrations import (
    KNOWLEDGE_MIGRATIONS,
    KnowledgeMigration,
    migrate_knowledge_schema,
    validate_knowledge_migrations,
)
from app.services.knowledge.db import sqlalchemy_knowledge_database_url
from app.services.knowledge.repository import PostgreSQLKnowledgeRepository


def test_knowledge_models_define_core_fact_source_tables() -> None:
    assert set(Base.metadata.tables) == {
        "documents",
        "document_versions",
        "assets",
        "chunks",
        "embeddings",
        "evidence_ledger",
        "ingestion_jobs",
    }
    assert "active" in Base.metadata.tables["documents"].columns
    assert "document_id" in Base.metadata.tables["document_versions"].columns
    assert "embedding" in Base.metadata.tables["embeddings"].columns
    assert "trace_id" in Base.metadata.tables["evidence_ledger"].columns
    assert "source_id" in Base.metadata.tables["evidence_ledger"].columns


def test_sqlalchemy_knowledge_url_uses_psycopg_driver() -> None:
    assert sqlalchemy_database_url(
        "postgresql://user:pass@localhost/db"
    ) == "postgresql+psycopg://user:pass@localhost/db"
    assert sqlalchemy_knowledge_database_url(
        "postgresql+psycopg://user:pass@localhost/db"
    ) == "postgresql+psycopg://user:pass@localhost/db"


def test_knowledge_migration_defines_core_tables_and_pgvector() -> None:
    migration = KNOWLEDGE_MIGRATIONS[0]
    rendered = "\n".join(migration.statements)

    assert [item.version for item in KNOWLEDGE_MIGRATIONS] == [1]
    assert migration.name == "knowledge_core_schema"
    assert "CREATE EXTENSION IF NOT EXISTS vector" in rendered
    for table in (
        "documents",
        "document_versions",
        "assets",
        "chunks",
        "embeddings",
        "evidence_ledger",
        "ingestion_jobs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in rendered
    assert "active BOOLEAN NOT NULL DEFAULT FALSE" in rendered
    assert "active_on_success BOOLEAN NOT NULL DEFAULT FALSE" in rendered


def test_validate_knowledge_migrations_rejects_bad_definitions() -> None:
    with pytest.raises(ValueError):
        validate_knowledge_migrations(
            (
                KnowledgeMigration(1, "one", ("SELECT 1",)),
                KnowledgeMigration(1, "two", ("SELECT 2",)),
            )
        )
    with pytest.raises(ValueError):
        validate_knowledge_migrations((KnowledgeMigration(0, "bad", ("SELECT 1",)),))
    with pytest.raises(ValueError):
        validate_knowledge_migrations((KnowledgeMigration(1, "", ("SELECT 1",)),))
    with pytest.raises(ValueError):
        validate_knowledge_migrations((KnowledgeMigration(1, "empty", ()),))


def test_migrate_knowledge_schema_records_checksums() -> None:
    conn = _FakeConnection()
    migrations = (
        KnowledgeMigration(2, "second", ("SELECT 2",)),
        KnowledgeMigration(1, "first", ("SELECT 1",)),
    )

    migrate_knowledge_schema(conn, migrations)

    assert list(conn.records) == [1, 2]
    assert conn.records[1]["checksum"] == migrations[1].checksum
    assert conn.records[2]["checksum"] == migrations[0].checksum


def test_knowledge_active_by_default_is_false() -> None:
    assert settings.knowledge_active_by_default is False


def test_knowledge_repository_document_defaults_to_inactive() -> None:
    params = DocumentRepository.document_params(
        document_id="doc-1",
        source_uri="manual.pdf",
    )

    assert params["active"] is False


def test_knowledge_repository_evidence_row_detects_placeholder() -> None:
    row = EvidenceLedgerRepository.evidence_row(
        EvidenceItem(
            source="manual::degraded",
            snippet="no manual evidence",
            metadata={"retriever": "manual_lookup-degraded"},
        ),
        trace_id="trace-1",
        run_id="run-1",
        runtime_request_id="req-1",
        retrieval_method=None,
    )

    assert row["trace_id"] == "trace-1"
    assert row["runtime_request_id"] == "req-1"
    assert row["is_placeholder"] is True
    assert row["retrieval_method"] == "manual_lookup-degraded"


def test_legacy_knowledge_repository_compatibility_helpers() -> None:
    params = PostgreSQLKnowledgeRepository._document_params(
        document_id="doc-1",
        source_uri="manual.pdf",
    )
    row = PostgreSQLKnowledgeRepository._evidence_row(
        {"source": "manual", "snippet": "s", "metadata": {"chunk_id": "c1"}},
        trace_id=None,
        run_id=None,
        runtime_request_id="req-1",
        retrieval_method="manual_lookup",
    )

    assert params["active"] is False
    assert row["chunk_id"] == "c1"


class _FakeConnection:
    def __init__(self, records=None) -> None:
        self.records = dict(records or {})
        self.executed: list[str] = []
        self.params: list[object] = []
        self._fetchall: list[tuple] = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None):
        normalized = " ".join(str(sql).split())
        self.conn.executed.append(str(sql).strip())
        self.conn.params.append(params)
        if normalized.startswith("SELECT version, name, checksum FROM knowledge_schema_migrations"):
            self.conn._fetchall = [
                (version, record["name"], record["checksum"])
                for version, record in self.conn.records.items()
            ]
            return
        if normalized.startswith("INSERT INTO knowledge_schema_migrations"):
            version, name, checksum = params
            self.conn.records[int(version)] = {"name": name, "checksum": checksum}

    def fetchall(self):
        return self.conn._fetchall
