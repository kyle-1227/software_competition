from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


LEGACY_V1_CHECKSUM = "trace-observability-backbone-v1"


@dataclass(frozen=True)
class TraceMigration:
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


def ensure_migration_table(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(_MIGRATION_TABLE_SQL)
        cur.execute(_MIGRATION_CHECKSUM_COLUMN_SQL)


def validate_migrations(migrations: Iterable[TraceMigration]) -> list[TraceMigration]:
    ordered = sorted(migrations, key=lambda item: item.version)
    seen: set[int] = set()
    for migration in ordered:
        if migration.version <= 0:
            raise ValueError("Trace migration version must be a positive integer")
        if migration.version in seen:
            raise ValueError(f"Duplicate trace migration version: {migration.version}")
        if not migration.name.strip():
            raise ValueError(f"Trace migration {migration.version} has an empty name")
        if not migration.statements:
            raise ValueError(f"Trace migration {migration.version} has no statements")
        seen.add(migration.version)
    return ordered


def migrate_trace_schema(
    conn: Any,
    migrations: Iterable[TraceMigration] | None = None,
) -> None:
    ordered = validate_migrations(TRACE_MIGRATIONS if migrations is None else tuple(migrations))
    ensure_migration_table(conn)
    applied = _load_applied_migrations(conn)
    with conn.cursor() as cur:
        for migration in ordered:
            record = applied.get(migration.version)
            if record is None:
                _execute_statements(cur, migration.statements)
                cur.execute(
                    """
                    INSERT INTO trace_schema_migrations(version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
                continue

            checksum = record.get("checksum")
            if checksum == migration.checksum:
                continue
            if _can_backfill_legacy_checksum(migration, checksum):
                _execute_statements(cur, migration.statements)
                cur.execute(
                    """
                    UPDATE trace_schema_migrations
                    SET name = %s, checksum = %s
                    WHERE version = %s
                    """,
                    (migration.name, migration.checksum, migration.version),
                )
                continue
            raise RuntimeError(
                "Trace migration checksum mismatch for version "
                f"{migration.version}: applied={checksum!r} expected={migration.checksum!r}"
            )


def _load_applied_migrations(conn: Any) -> dict[int, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum FROM trace_schema_migrations")
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


def _can_backfill_legacy_checksum(
    migration: TraceMigration,
    checksum: str | None,
) -> bool:
    return migration.version == 1 and checksum in {None, LEGACY_V1_CHECKSUM}


def _normalize_sql(statement: str) -> str:
    return "\n".join(str(statement or "").strip().splitlines())


_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trace_schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_MIGRATION_CHECKSUM_COLUMN_SQL = """
ALTER TABLE trace_schema_migrations
  ADD COLUMN IF NOT EXISTS checksum TEXT
"""

_TRACE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_traces (
  trace_id TEXT PRIMARY KEY,
  run_id TEXT,
  session_id TEXT NOT NULL,
  user_id TEXT,
  question TEXT,
  normalized_question TEXT,
  app_env TEXT,
  app_version TEXT,
  git_commit TEXT,
  llm_provider TEXT,
  llm_model TEXT,
  embedding_model TEXT,
  reranker_model TEXT,
  manual_id TEXT,
  index_version TEXT,
  index_sha256 TEXT,
  feature_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'running',
  final_answer_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  total_duration_ms DOUBLE PRECISION
)
"""

_SPAN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_trace_spans (
  span_id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL REFERENCES agent_traces(trace_id) ON DELETE CASCADE,
  parent_span_id TEXT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  start_time TIMESTAMPTZ NOT NULL,
  end_time TIMESTAMPTZ,
  duration_ms DOUBLE PRECISION,
  inputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  outputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  error_type TEXT,
  attempt INTEGER,
  retry_count INTEGER,
  fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
  degraded BOOLEAN NOT NULL DEFAULT FALSE,
  token_usage_json JSONB,
  cost_estimate_json JSONB,
  quality_json JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_ALTER_TRACE_BASE_COLUMNS_SQL = """
ALTER TABLE agent_traces
  ADD COLUMN IF NOT EXISTS run_id TEXT,
  ADD COLUMN IF NOT EXISTS user_id TEXT,
  ADD COLUMN IF NOT EXISTS question TEXT,
  ADD COLUMN IF NOT EXISTS normalized_question TEXT,
  ADD COLUMN IF NOT EXISTS app_env TEXT,
  ADD COLUMN IF NOT EXISTS app_version TEXT,
  ADD COLUMN IF NOT EXISTS git_commit TEXT,
  ADD COLUMN IF NOT EXISTS llm_provider TEXT,
  ADD COLUMN IF NOT EXISTS llm_model TEXT,
  ADD COLUMN IF NOT EXISTS embedding_model TEXT,
  ADD COLUMN IF NOT EXISTS reranker_model TEXT,
  ADD COLUMN IF NOT EXISTS manual_id TEXT,
  ADD COLUMN IF NOT EXISTS index_version TEXT,
  ADD COLUMN IF NOT EXISTS index_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS feature_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running',
  ADD COLUMN IF NOT EXISTS final_answer_hash TEXT,
  ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS total_duration_ms DOUBLE PRECISION
"""

_ALTER_SPAN_COLUMNS_SQL = """
ALTER TABLE agent_trace_spans
  ADD COLUMN IF NOT EXISTS parent_span_id TEXT,
  ADD COLUMN IF NOT EXISTS duration_ms DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS inputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS outputs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS error TEXT,
  ADD COLUMN IF NOT EXISTS error_type TEXT,
  ADD COLUMN IF NOT EXISTS attempt INTEGER,
  ADD COLUMN IF NOT EXISTS retry_count INTEGER,
  ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS token_usage_json JSONB,
  ADD COLUMN IF NOT EXISTS cost_estimate_json JSONB,
  ADD COLUMN IF NOT EXISTS quality_json JSONB,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()
"""

_ALTER_QUESTION_COLUMNS_SQL = """
ALTER TABLE agent_traces
  ADD COLUMN IF NOT EXISTS question_hash TEXT,
  ADD COLUMN IF NOT EXISTS question_preview TEXT,
  ADD COLUMN IF NOT EXISTS question_length INTEGER
"""

_NORMALIZE_TRACE_STATUS_SQL = """
UPDATE agent_traces
SET status = CASE
  WHEN status = 'ok' THEN 'success'
  WHEN status IN ('running', 'success', 'error', 'cancelled') THEN status
  ELSE 'error'
END
"""

_NORMALIZE_SPAN_STATUS_SQL = """
UPDATE agent_trace_spans
SET status = CASE
  WHEN status IN ('ok', 'error', 'skipped') THEN status
  ELSE 'error'
END
"""

_TRACE_STATUS_CONSTRAINT_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_agent_traces_status'
      AND conrelid = 'agent_traces'::regclass
  ) THEN
    ALTER TABLE agent_traces
      ADD CONSTRAINT chk_agent_traces_status
      CHECK (status IN ('running', 'success', 'error', 'cancelled'));
  END IF;
END $$;
"""

_SPAN_STATUS_CONSTRAINT_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'chk_agent_trace_spans_status'
      AND conrelid = 'agent_trace_spans'::regclass
  ) THEN
    ALTER TABLE agent_trace_spans
      ADD CONSTRAINT chk_agent_trace_spans_status
      CHECK (status IN ('ok', 'error', 'skipped'));
  END IF;
END $$;
"""

_BASE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_agent_traces_session_id ON agent_traces(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_traces_created_at ON agent_traces(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_traces_status ON agent_traces(status)",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_trace_id ON agent_trace_spans(trace_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_kind ON agent_trace_spans(kind)",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_name ON agent_trace_spans(name)",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_status ON agent_trace_spans(status)",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_degraded ON agent_trace_spans(degraded) WHERE degraded = TRUE",
    "CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_fallback_used ON agent_trace_spans(fallback_used) WHERE fallback_used = TRUE",
)

_QUESTION_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_agent_traces_question_hash ON agent_traces(question_hash)",
)

TRACE_MIGRATIONS = (
    TraceMigration(
        version=1,
        name="trace_base_schema",
        statements=(
            _TRACE_TABLE_SQL,
            _SPAN_TABLE_SQL,
            _ALTER_TRACE_BASE_COLUMNS_SQL,
            _ALTER_SPAN_COLUMNS_SQL,
            _NORMALIZE_TRACE_STATUS_SQL,
            _NORMALIZE_SPAN_STATUS_SQL,
            _TRACE_STATUS_CONSTRAINT_SQL,
            _SPAN_STATUS_CONSTRAINT_SQL,
            *_BASE_INDEX_SQL,
        ),
    ),
    TraceMigration(
        version=2,
        name="question_persistence_fields",
        statements=(
            _ALTER_QUESTION_COLUMNS_SQL,
            *_QUESTION_INDEX_SQL,
        ),
    ),
)
