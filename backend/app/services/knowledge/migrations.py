from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class KnowledgeMigration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = {
            "version": self.version,
            "name": self.name,
            "statements": [_normalize_sql(statement) for statement in self.statements],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def ensure_knowledge_migration_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(_MIGRATION_TABLE_SQL)
        cur.execute(_MIGRATION_CHECKSUM_COLUMN_SQL)


def validate_knowledge_migrations(
    migrations: Iterable[KnowledgeMigration],
) -> list[KnowledgeMigration]:
    ordered = sorted(migrations, key=lambda item: item.version)
    seen: set[int] = set()
    for migration in ordered:
        if migration.version <= 0:
            raise ValueError("Knowledge migration version must be a positive integer")
        if migration.version in seen:
            raise ValueError(f"Duplicate knowledge migration version: {migration.version}")
        if not migration.name.strip():
            raise ValueError(f"Knowledge migration {migration.version} has an empty name")
        if not migration.statements:
            raise ValueError(f"Knowledge migration {migration.version} has no statements")
        seen.add(migration.version)
    return ordered


def migrate_knowledge_schema(
    conn: Any,
    migrations: Iterable[KnowledgeMigration] | None = None,
) -> None:
    ordered = validate_knowledge_migrations(
        KNOWLEDGE_MIGRATIONS if migrations is None else tuple(migrations)
    )
    ensure_knowledge_migration_table(conn)
    applied = _load_applied_migrations(conn)
    with conn.cursor() as cur:
        for migration in ordered:
            record = applied.get(migration.version)
            if record is None:
                _execute_statements(cur, migration.statements)
                cur.execute(
                    """
                    INSERT INTO knowledge_schema_migrations(version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
                continue
            if record.get("checksum") == migration.checksum:
                continue
            raise RuntimeError(
                "Knowledge migration checksum mismatch for version "
                f"{migration.version}: applied={record.get('checksum')!r} "
                f"expected={migration.checksum!r}"
            )


def _load_applied_migrations(conn: Any) -> dict[int, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum FROM knowledge_schema_migrations")
        rows = cur.fetchall()
    applied: dict[int, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict):
            version = int(row["version"])
            applied[version] = {
                "name": row.get("name"),
                "checksum": row.get("checksum"),
            }
        else:
            version = int(row[0])
            applied[version] = {
                "name": row[1] if len(row) > 1 else None,
                "checksum": row[2] if len(row) > 2 else None,
            }
    return applied


def _execute_statements(cur: Any, statements: Iterable[str]) -> None:
    for statement in statements:
        cur.execute(statement)


def _normalize_sql(statement: str) -> str:
    return "\n".join(str(statement or "").strip().splitlines())


_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_MIGRATION_CHECKSUM_COLUMN_SQL = """
ALTER TABLE knowledge_schema_migrations
  ADD COLUMN IF NOT EXISTS checksum TEXT
"""

_PGVECTOR_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector"

_DOCUMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  source_uri TEXT NOT NULL,
  source_type TEXT NOT NULL DEFAULT 'manual',
  title TEXT,
  mime_type TEXT,
  sha256 TEXT,
  device_name TEXT,
  device_model TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  active BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_documents_status
    CHECK (status IN ('pending', 'processing', 'ready', 'failed', 'archived'))
)
"""

_ASSETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  asset_type TEXT NOT NULL,
  uri TEXT NOT NULL,
  mime_type TEXT,
  sha256 TEXT,
  page_number INTEGER,
  width INTEGER,
  height INTEGER,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DOCUMENT_VERSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS document_versions (
  document_version_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  version INTEGER NOT NULL DEFAULT 1,
  source_uri TEXT NOT NULL,
  sha256 TEXT,
  parser_name TEXT,
  parser_version TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  active BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_document_versions_version UNIQUE (document_id, version)
)
"""

_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  document_version_id TEXT REFERENCES document_versions(document_version_id) ON DELETE SET NULL,
  asset_id TEXT REFERENCES assets(asset_id) ON DELETE SET NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  page INTEGER,
  token_count INTEGER,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_chunks_document_index UNIQUE (document_id, chunk_index)
)
"""

_EMBEDDINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embeddings (
  embedding_id TEXT PRIMARY KEY,
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  dimensions INTEGER,
  embedding vector NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_embeddings_chunk_model UNIQUE (chunk_id, provider, model)
)
"""

_EVIDENCE_LEDGER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS evidence_ledger (
  evidence_id TEXT PRIMARY KEY,
  trace_id TEXT REFERENCES agent_traces(trace_id) ON DELETE SET NULL,
  run_id TEXT,
  runtime_request_id TEXT,
  document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
  document_version_id TEXT REFERENCES document_versions(document_version_id) ON DELETE SET NULL,
  chunk_id TEXT REFERENCES chunks(chunk_id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  source_id TEXT,
  page INTEGER,
  snippet TEXT NOT NULL,
  score DOUBLE PRECISION,
  retrieval_method TEXT,
  is_placeholder BOOLEAN NOT NULL DEFAULT FALSE,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_INGESTION_JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  job_id TEXT PRIMARY KEY,
  document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
  document_version_id TEXT REFERENCES document_versions(document_version_id) ON DELETE SET NULL,
  source_uri TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  active_on_success BOOLEAN NOT NULL DEFAULT FALSE,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_ingestion_jobs_status
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'))
)
"""

_KNOWLEDGE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_documents_source_uri ON documents(source_uri)",
    "CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_documents_active ON documents(active)",
    "CREATE INDEX IF NOT EXISTS idx_assets_document_id ON assets(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_assets_asset_type ON assets(asset_type)",
    "CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_document_versions_document_id ON document_versions(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_document_versions_sha256 ON document_versions(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_document_versions_active ON document_versions(active)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document_version_id ON chunks(document_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_asset_id ON chunks(asset_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_active ON chunks(active)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_chunk_id ON embeddings(chunk_id)",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(provider, model)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_trace_id ON evidence_ledger(trace_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_runtime_request_id ON evidence_ledger(runtime_request_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_chunk_id ON evidence_ledger(chunk_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_document_id ON evidence_ledger(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_document_version_id ON evidence_ledger(document_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_ledger_source_id ON evidence_ledger(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document_id ON ingestion_jobs(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_document_version_id ON ingestion_jobs(document_version_id)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_source_uri ON ingestion_jobs(source_uri)",
)

KNOWLEDGE_MIGRATIONS = (
    KnowledgeMigration(
        version=1,
        name="knowledge_core_schema",
        statements=(
            _PGVECTOR_EXTENSION_SQL,
            _DOCUMENTS_TABLE_SQL,
            _ASSETS_TABLE_SQL,
            _DOCUMENT_VERSIONS_TABLE_SQL,
            _CHUNKS_TABLE_SQL,
            _EMBEDDINGS_TABLE_SQL,
            _EVIDENCE_LEDGER_TABLE_SQL,
            _INGESTION_JOBS_TABLE_SQL,
            *_KNOWLEDGE_INDEX_SQL,
        ),
    ),
)
