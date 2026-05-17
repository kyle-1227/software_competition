from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan
from app.services.tracing.serializers import sanitize_trace_dict

logger = logging.getLogger(__name__)


class TraceRepository(Protocol):
    def initialize(self) -> None: ...

    def save_trace(self, trace: Trace) -> None: ...

    def save_span(self, trace_id: str, span: TraceSpan) -> None: ...

    def close_trace(self, trace: Trace) -> None: ...

    def get_trace(self, trace_id: str) -> Trace | None: ...

    def list_traces(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[Trace]: ...

    def list_spans(self, trace_id: str) -> list[TraceSpan]: ...

    def healthcheck(self) -> bool: ...


class JsonlTraceRepository:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = _resolve_storage_path(storage_path)

    def initialize(self) -> None:
        try:
            self.storage_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("JSONL trace storage unavailable: %s", exc)

    def save_trace(self, trace: Trace) -> None:
        del trace

    def save_span(self, trace_id: str, span: TraceSpan) -> None:
        del trace_id, span

    def close_trace(self, trace: Trace) -> None:
        self.initialize()
        filepath = self.storage_path / "traces.jsonl"
        with filepath.open("a", encoding="utf-8") as handle:
            handle.write(trace.model_dump_json() + "\n")

    def get_trace(self, trace_id: str) -> Trace | None:
        filepath = self.storage_path / "traces.jsonl"
        if not filepath.exists():
            return None
        try:
            with filepath.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        trace = Trace.model_validate(json.loads(line))
                    except Exception:
                        continue
                    if trace.trace_id == trace_id:
                        return trace
        except OSError:
            return None
        return None

    def list_traces(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[Trace]:
        filepath = self.storage_path / "traces.jsonl"
        if not filepath.exists():
            return []
        traces: list[Trace] = []
        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                trace = Trace.model_validate(json.loads(line))
            except Exception:
                continue
            if session_id and trace.session_id != session_id:
                continue
            if status and trace.status != status:
                continue
            traces.append(trace)
            if len(traces) >= limit:
                break
        return traces

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        trace = self.get_trace(trace_id)
        if trace is None:
            return []
        return [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]

    def healthcheck(self) -> bool:
        try:
            self.initialize()
            return self.storage_path.exists()
        except OSError:
            return False


class PostgreSQLTraceRepository:
    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("PostgreSQL trace repository requires a database URL")
        self.database_url = database_url

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_TRACE_TABLE_SQL)
                cur.execute(_SPAN_TABLE_SQL)
                for statement in _INDEX_SQL:
                    cur.execute(statement)

    def save_trace(self, trace: Trace) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_traces (
                        trace_id, run_id, session_id, user_id, question,
                        normalized_question, app_env, app_version, git_commit,
                        llm_provider, llm_model, embedding_model, reranker_model,
                        manual_id, index_version, index_sha256, feature_flags_json,
                        status, final_answer_hash, created_at, closed_at,
                        total_duration_ms
                    )
                    VALUES (
                        %(trace_id)s, %(run_id)s, %(session_id)s, %(user_id)s,
                        %(question)s, %(normalized_question)s, %(app_env)s,
                        %(app_version)s, %(git_commit)s, %(llm_provider)s,
                        %(llm_model)s, %(embedding_model)s, %(reranker_model)s,
                        %(manual_id)s, %(index_version)s, %(index_sha256)s,
                        %(feature_flags_json)s, %(status)s, %(final_answer_hash)s,
                        %(created_at)s, %(closed_at)s, %(total_duration_ms)s
                    )
                    ON CONFLICT (trace_id) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        session_id = EXCLUDED.session_id,
                        user_id = EXCLUDED.user_id,
                        question = EXCLUDED.question,
                        normalized_question = EXCLUDED.normalized_question,
                        app_env = EXCLUDED.app_env,
                        app_version = EXCLUDED.app_version,
                        git_commit = EXCLUDED.git_commit,
                        llm_provider = EXCLUDED.llm_provider,
                        llm_model = EXCLUDED.llm_model,
                        embedding_model = EXCLUDED.embedding_model,
                        reranker_model = EXCLUDED.reranker_model,
                        manual_id = EXCLUDED.manual_id,
                        index_version = EXCLUDED.index_version,
                        index_sha256 = EXCLUDED.index_sha256,
                        feature_flags_json = EXCLUDED.feature_flags_json,
                        status = EXCLUDED.status,
                        final_answer_hash = EXCLUDED.final_answer_hash,
                        closed_at = EXCLUDED.closed_at,
                        total_duration_ms = EXCLUDED.total_duration_ms
                    """,
                    {
                        **_trace_row(trace),
                        "feature_flags_json": Jsonb(trace.feature_flags or {}),
                    },
                )

    def save_span(self, trace_id: str, span: TraceSpan) -> None:
        from psycopg.types.json import Jsonb

        inputs = sanitize_trace_dict(span.inputs)
        outputs = sanitize_trace_dict(span.outputs)
        metadata = sanitize_trace_dict(span.metadata)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_trace_spans (
                        span_id, trace_id, parent_span_id, name, kind, status,
                        start_time, end_time, duration_ms, inputs_json,
                        outputs_json, metadata_json, error, error_type, attempt,
                        retry_count, fallback_used, degraded, token_usage_json,
                        cost_estimate_json, quality_json, created_at
                    )
                    VALUES (
                        %(span_id)s, %(trace_id)s, %(parent_span_id)s, %(name)s,
                        %(kind)s, %(status)s, %(start_time)s, %(end_time)s,
                        %(duration_ms)s, %(inputs_json)s, %(outputs_json)s,
                        %(metadata_json)s, %(error)s, %(error_type)s,
                        %(attempt)s, %(retry_count)s, %(fallback_used)s,
                        %(degraded)s, %(token_usage_json)s, %(cost_estimate_json)s,
                        %(quality_json)s, %(created_at)s
                    )
                    ON CONFLICT (span_id) DO UPDATE SET
                        parent_span_id = EXCLUDED.parent_span_id,
                        name = EXCLUDED.name,
                        kind = EXCLUDED.kind,
                        status = EXCLUDED.status,
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        duration_ms = EXCLUDED.duration_ms,
                        inputs_json = EXCLUDED.inputs_json,
                        outputs_json = EXCLUDED.outputs_json,
                        metadata_json = EXCLUDED.metadata_json,
                        error = EXCLUDED.error,
                        error_type = EXCLUDED.error_type,
                        attempt = EXCLUDED.attempt,
                        retry_count = EXCLUDED.retry_count,
                        fallback_used = EXCLUDED.fallback_used,
                        degraded = EXCLUDED.degraded,
                        token_usage_json = EXCLUDED.token_usage_json,
                        cost_estimate_json = EXCLUDED.cost_estimate_json,
                        quality_json = EXCLUDED.quality_json
                    """,
                    {
                        "span_id": span.span_id,
                        "trace_id": trace_id,
                        "parent_span_id": span.parent_span_id,
                        "name": span.name,
                        "kind": _enum_value(span.kind),
                        "status": _enum_value(span.status),
                        "start_time": span.start_time,
                        "end_time": span.end_time,
                        "duration_ms": span.duration_ms,
                        "inputs_json": Jsonb(inputs),
                        "outputs_json": Jsonb(outputs),
                        "metadata_json": Jsonb(metadata),
                        "error": span.error,
                        "error_type": span.error_type,
                        "attempt": span.attempt,
                        "retry_count": span.retry_count,
                        "fallback_used": span.fallback_used,
                        "degraded": span.degraded,
                        "token_usage_json": Jsonb(span.token_usage) if span.token_usage else None,
                        "cost_estimate_json": Jsonb(span.cost_estimate) if span.cost_estimate else None,
                        "quality_json": Jsonb(span.quality) if span.quality else None,
                        "created_at": datetime.now(timezone.utc),
                    },
                )

    def close_trace(self, trace: Trace) -> None:
        self.save_trace(trace)

    def get_trace(self, trace_id: str) -> Trace | None:
        trace_row = None
        span_rows: list[dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute("SELECT * FROM agent_traces WHERE trace_id = %s", (trace_id,))
                trace_row = cur.fetchone()
                if trace_row is None:
                    return None
                cur.execute(
                    "SELECT * FROM agent_trace_spans WHERE trace_id = %s ORDER BY start_time ASC",
                    (trace_id,),
                )
                span_rows = list(cur.fetchall())
        return _trace_from_rows(trace_row, span_rows)

    def list_traces(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[Trace]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = %s")
            params.append(session_id)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit or 50), 200)))
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute(
                    f"SELECT * FROM agent_traces{where} ORDER BY created_at DESC LIMIT %s",
                    params,
                )
                return [_trace_from_rows(row, []) for row in cur.fetchall()]

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute(
                    "SELECT * FROM agent_trace_spans WHERE trace_id = %s ORDER BY start_time ASC",
                    (trace_id,),
                )
                return [_span_from_row(row) for row in cur.fetchall()]

    def healthcheck(self) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(
                "psycopg is required for PostgreSQL trace backend"
            ) from exc
        return psycopg.connect(self.database_url, autocommit=True)

    @staticmethod
    def _dict_row():
        from psycopg.rows import dict_row

        return dict_row


def build_trace_repository(storage_path: Path | None = None) -> TraceRepository:
    if storage_path is not None:
        repository: TraceRepository = JsonlTraceRepository(storage_path)
        repository.initialize()
        return repository

    backend = getattr(settings, "trace_backend", "auto")
    database_url = getattr(settings, "trace_database_url", None) or getattr(
        settings, "database_url", None
    )
    if backend == "jsonl":
        repository = JsonlTraceRepository()
        repository.initialize()
        return repository
    if backend == "postgres":
        repository = PostgreSQLTraceRepository(str(database_url or ""))
        repository.initialize()
        return repository
    if database_url:
        try:
            repository = PostgreSQLTraceRepository(str(database_url))
            repository.initialize()
            return repository
        except Exception as exc:
            logger.warning("PostgreSQL trace backend unavailable, falling back to JSONL: %s", exc)
    repository = JsonlTraceRepository()
    repository.initialize()
    return repository


def _resolve_storage_path(storage_path: Path | None) -> Path:
    storage = storage_path
    if storage is None:
        storage_path_str = getattr(settings, "trace_storage_path", "../data/traces")
        storage = Path(storage_path_str)
        if not storage.is_absolute():
            storage = Path(__file__).resolve().parents[4] / storage_path_str
    return Path(storage)


def _walk_spans(span: TraceSpan):
    yield span
    for child in span.children:
        yield from _walk_spans(child)


def _trace_row(trace: Trace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "session_id": trace.session_id,
        "user_id": trace.user_id,
        "question": trace.question,
        "normalized_question": trace.normalized_question,
        "app_env": trace.app_env,
        "app_version": trace.app_version,
        "git_commit": trace.git_commit,
        "llm_provider": trace.llm_provider,
        "llm_model": trace.llm_model,
        "embedding_model": trace.embedding_model,
        "reranker_model": trace.reranker_model,
        "manual_id": trace.manual_id,
        "index_version": trace.index_version,
        "index_sha256": trace.index_sha256,
        "status": trace.status,
        "final_answer_hash": trace.final_answer_hash,
        "created_at": trace.created_at,
        "closed_at": trace.closed_at,
        "total_duration_ms": trace.total_duration_ms,
    }


def _trace_from_rows(trace_row: dict[str, Any], span_rows: list[dict[str, Any]]) -> Trace:
    root = TraceSpan(
        span_id=f"{trace_row['trace_id']}:root",
        trace_id=trace_row["trace_id"],
        name="harness",
        kind=SpanKind.AGENT,
        start_time=trace_row["created_at"],
        end_time=trace_row.get("closed_at"),
        duration_ms=trace_row.get("total_duration_ms"),
    )
    spans = [_span_from_row(row) for row in span_rows]
    by_id = {span.span_id: span for span in spans}
    for span in spans:
        parent = by_id.get(span.parent_span_id or "")
        if parent is None:
            root.children.append(span)
        else:
            parent.children.append(span)
    return Trace(
        trace_id=trace_row["trace_id"],
        run_id=trace_row.get("run_id"),
        session_id=trace_row.get("session_id") or "",
        user_id=trace_row.get("user_id"),
        question=trace_row.get("question") or "",
        normalized_question=trace_row.get("normalized_question"),
        app_env=trace_row.get("app_env"),
        app_version=trace_row.get("app_version"),
        git_commit=trace_row.get("git_commit"),
        llm_provider=trace_row.get("llm_provider"),
        llm_model=trace_row.get("llm_model"),
        embedding_model=trace_row.get("embedding_model"),
        reranker_model=trace_row.get("reranker_model"),
        manual_id=trace_row.get("manual_id"),
        index_version=trace_row.get("index_version"),
        index_sha256=trace_row.get("index_sha256"),
        feature_flags=trace_row.get("feature_flags_json") or {},
        status=trace_row.get("status") or "running",
        final_answer_hash=trace_row.get("final_answer_hash"),
        root_span=root,
        created_at=trace_row["created_at"],
        closed_at=trace_row.get("closed_at"),
        total_duration_ms=trace_row.get("total_duration_ms"),
    )


def _span_from_row(row: dict[str, Any]) -> TraceSpan:
    return TraceSpan(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row.get("parent_span_id"),
        name=row["name"],
        kind=SpanKind(row["kind"]),
        start_time=row["start_time"],
        end_time=row.get("end_time"),
        duration_ms=row.get("duration_ms"),
        inputs=row.get("inputs_json") or {},
        outputs=row.get("outputs_json") or {},
        status=SpanStatus(row.get("status") or SpanStatus.OK.value),
        error=row.get("error"),
        error_type=row.get("error_type"),
        attempt=row.get("attempt"),
        retry_count=row.get("retry_count"),
        fallback_used=bool(row.get("fallback_used")),
        degraded=bool(row.get("degraded")),
        token_usage=row.get("token_usage_json"),
        cost_estimate=row.get("cost_estimate_json"),
        quality=row.get("quality_json"),
        metadata=row.get("metadata_json") or {},
    )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


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

_INDEX_SQL = (
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
