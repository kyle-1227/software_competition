from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan, TraceStatus
from app.services.tracing.migrations import migrate_trace_schema
from app.services.tracing.persistence import (
    question_persistence_fields,
    sanitize_span_for_persistence,
    sanitize_trace_for_persistence,
)
from app.services.tracing.serializers import (
    resolve_capture_mode,
)

logger = logging.getLogger(__name__)


@dataclass
class RepositoryHealth:
    backend: str
    configured_backend: str
    healthy: bool
    degraded: bool = False
    ever_degraded: bool = False
    last_error: str | None = None
    last_error_at: datetime | None = None
    last_success_at: datetime | None = None
    storage_path: str | None = None
    database_url_configured: bool = False
    capture_mode: str = "summary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "configured_backend": self.configured_backend,
            "healthy": self.healthy,
            "degraded": self.degraded,
            "ever_degraded": self.ever_degraded,
            "last_error": self.last_error,
            "last_error_at": _datetime_to_json(self.last_error_at),
            "last_success_at": _datetime_to_json(self.last_success_at),
            "storage_path": self.storage_path,
            "database_url_configured": self.database_url_configured,
            "capture_mode": self.capture_mode,
        }


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

    def health_status(self) -> RepositoryHealth: ...

    def list_trace_summaries(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def delete_traces(self, trace_ids: list[str], batch_size: int = 500) -> int: ...


class JsonlTraceRepository:
    def __init__(
        self,
        storage_path: Path | None = None,
        *,
        configured_backend: str = "jsonl",
        degraded: bool = False,
        last_error: str | None = None,
    ) -> None:
        self.storage_path = _resolve_storage_path(storage_path)
        self.configured_backend = configured_backend
        self._fallback_degraded = degraded
        self._fallback_error = last_error
        self.degraded = degraded
        self.ever_degraded = degraded or bool(last_error)
        self.last_error = last_error
        self.last_error_at = datetime.now(timezone.utc) if last_error else None
        self.last_success_at: datetime | None = None

    def initialize(self) -> None:
        try:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self._record_success()
        except OSError as exc:
            self._record_error(exc)
            logger.warning("JSONL trace storage unavailable: %s", exc)

    def save_trace(self, trace: Trace) -> None:
        del trace
        self._record_success()

    def save_span(self, trace_id: str, span: TraceSpan) -> None:
        del trace_id, span
        self._record_success()

    def close_trace(self, trace: Trace) -> None:
        try:
            self.initialize()
            filepath = self.storage_path / "traces.jsonl"
            payload = sanitize_trace_for_persistence(trace)
            with filepath.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
            self._record_success()
        except Exception as exc:
            self._record_error(exc)
            raise

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
            if status and _enum_value(trace.status) != _normalize_trace_status_value(status):
                continue
            traces.append(trace)
            if len(traces) >= limit:
                break
        return traces

    def list_trace_summaries(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        from app.services.tracing.summary import build_trace_summary

        items: list[dict[str, Any]] = []
        for trace in self.list_traces(limit=limit, session_id=session_id, status=status):
            summary = build_trace_summary(trace)
            spans = [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]
            fallback_used = any(_span_flag(span, "fallback_used") for span in spans)
            degraded = any(_span_flag(span, "degraded") for span in spans)
            slowest = summary.get("slowest_spans") or []
            items.append(
                {
                    "trace_id": trace.trace_id,
                    "session_id": trace.session_id,
                    "status": _enum_value(trace.status),
                    "created_at": trace.created_at,
                    "closed_at": trace.closed_at,
                    "total_duration_ms": trace.total_duration_ms,
                    "question_preview": summary.get("question_preview"),
                    "span_count": summary.get("span_count", 0),
                    "error_count": summary.get("error_count", 0),
                    "degraded": degraded,
                    "fallback_used": fallback_used,
                    "slowest_span_name": slowest[0].get("name") if slowest else None,
                    "degraded_tool_names": summary.get("degraded_tool_names", []),
                }
            )
        return items

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        trace = self.get_trace(trace_id)
        if trace is None:
            return []
        return [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]

    def healthcheck(self) -> bool:
        try:
            self.initialize()
            healthy = self.storage_path.exists() and self.storage_path.is_dir()
            if healthy:
                self._record_success()
            return healthy
        except OSError as exc:
            self._record_error(exc)
            return False

    def health_status(self) -> RepositoryHealth:
        healthy = self.healthcheck()
        return RepositoryHealth(
            backend="jsonl",
            configured_backend=self.configured_backend,
            healthy=healthy,
            degraded=self.degraded or not healthy,
            ever_degraded=self.ever_degraded,
            last_error=self.last_error,
            last_error_at=self.last_error_at,
            last_success_at=self.last_success_at,
            storage_path=str(self.storage_path),
            database_url_configured=False,
            capture_mode=resolve_capture_mode(),
        )

    def delete_traces(self, trace_ids: list[str], batch_size: int = 500) -> int:
        del trace_ids, batch_size
        raise NotImplementedError("JSONL trace cleanup rewrites traces.jsonl via cleanup service")

    def _record_success(self) -> None:
        self.last_success_at = datetime.now(timezone.utc)
        self.degraded = self._fallback_degraded
        self.last_error = self._fallback_error if self._fallback_degraded else None

    def _record_error(self, exc: Exception) -> None:
        self.degraded = True
        self.ever_degraded = True
        self.last_error = str(exc)[:500]
        self.last_error_at = datetime.now(timezone.utc)


class PostgreSQLTraceRepository:
    def __init__(self, database_url: str, *, configured_backend: str = "postgres") -> None:
        if not database_url:
            raise ValueError("PostgreSQL trace repository requires a database URL")
        self.database_url = database_url
        self.configured_backend = configured_backend
        self.degraded = False
        self.ever_degraded = False
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None
        self.last_success_at: datetime | None = None

    def initialize(self) -> None:
        self.migrate_schema()

    def migrate_schema(self) -> None:
        with self._connect(autocommit=False) as conn:
            try:
                migrate_trace_schema(conn)
                conn.commit()
                self._record_success()
            except Exception as exc:
                conn.rollback()
                self._record_error(exc)
                raise

    def save_trace(self, trace: Trace) -> None:
        from psycopg.types.json import Jsonb

        try:
            persisted_trace = sanitize_trace_for_persistence(trace)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                    INSERT INTO agent_traces (
                        trace_id, run_id, session_id, user_id, question,
                        question_hash, question_preview, question_length,
                        normalized_question, app_env, app_version, git_commit,
                        llm_provider, llm_model, embedding_model, reranker_model,
                        manual_id, index_version, index_sha256, feature_flags_json,
                        status, final_answer_hash, created_at, closed_at,
                        total_duration_ms
                    )
                    VALUES (
                        %(trace_id)s, %(run_id)s, %(session_id)s, %(user_id)s,
                        %(question)s, %(question_hash)s, %(question_preview)s,
                        %(question_length)s, %(normalized_question)s, %(app_env)s,
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
                        question_hash = EXCLUDED.question_hash,
                        question_preview = EXCLUDED.question_preview,
                        question_length = EXCLUDED.question_length,
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
                            "feature_flags_json": Jsonb(persisted_trace.get("feature_flags") or {}),
                        },
                    )
            self._record_success()
        except Exception as exc:
            self._record_error(exc)
            raise

    def save_span(self, trace_id: str, span: TraceSpan) -> None:
        from psycopg.types.json import Jsonb

        try:
            persisted = sanitize_span_for_persistence(span)
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
                            "status": _normalize_span_status_value(span.status),
                            "start_time": span.start_time,
                            "end_time": span.end_time,
                            "duration_ms": span.duration_ms,
                            "inputs_json": Jsonb(persisted.get("inputs") or {}),
                            "outputs_json": Jsonb(persisted.get("outputs") or {}),
                            "metadata_json": Jsonb(persisted.get("metadata") or {}),
                            "error": persisted.get("error"),
                            "error_type": persisted.get("error_type"),
                            "attempt": persisted.get("attempt"),
                            "retry_count": persisted.get("retry_count"),
                            "fallback_used": bool(persisted.get("fallback_used")),
                            "degraded": bool(persisted.get("degraded")),
                            "token_usage_json": Jsonb(persisted.get("token_usage")) if persisted.get("token_usage") else None,
                            "cost_estimate_json": Jsonb(persisted.get("cost_estimate")) if persisted.get("cost_estimate") else None,
                            "quality_json": Jsonb(persisted.get("quality")) if persisted.get("quality") else None,
                            "created_at": datetime.now(timezone.utc),
                        },
                    )
            self._record_success()
        except Exception as exc:
            self._record_error(exc)
            raise

    def close_trace(self, trace: Trace) -> None:
        try:
            self.save_trace(trace)
            self._record_success()
        except Exception as exc:
            self._record_error(exc)
            raise

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
            params.append(_normalize_trace_status_value(status))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit or 50), 1000)))
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute(
                    f"SELECT * FROM agent_traces{where} ORDER BY created_at DESC LIMIT %s",
                    params,
                )
                return [_trace_from_rows(row, []) for row in cur.fetchall()]

    def list_trace_summaries(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("t.session_id = %s")
            params.append(session_id)
        if status:
            clauses.append("t.status = %s")
            params.append(_normalize_trace_status_value(status))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit or 50), 200)))
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute(
                    f"""
                    SELECT
                        t.trace_id,
                        t.session_id,
                        t.status,
                        t.created_at,
                        t.closed_at,
                        t.total_duration_ms,
                        t.question_preview,
                        COALESCE(stats.span_count, 0) AS span_count,
                        COALESCE(stats.error_count, 0) AS error_count,
                        COALESCE(stats.degraded, FALSE) AS degraded,
                        COALESCE(stats.fallback_used, FALSE) AS fallback_used,
                        slowest.name AS slowest_span_name
                    FROM agent_traces t
                    LEFT JOIN (
                        SELECT
                            trace_id,
                            COUNT(*)::int AS span_count,
                            COUNT(*) FILTER (WHERE status = 'error')::int AS error_count,
                            BOOL_OR(degraded) AS degraded,
                            BOOL_OR(fallback_used) AS fallback_used
                        FROM agent_trace_spans
                        GROUP BY trace_id
                    ) stats ON stats.trace_id = t.trace_id
                    LEFT JOIN LATERAL (
                        SELECT name
                        FROM agent_trace_spans s
                        WHERE s.trace_id = t.trace_id
                        ORDER BY duration_ms DESC NULLS LAST, start_time ASC
                        LIMIT 1
                    ) slowest ON TRUE
                    {where}
                    ORDER BY t.created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                return [
                    {
                        "trace_id": row["trace_id"],
                        "session_id": row["session_id"],
                        "status": row["status"],
                        "created_at": row["created_at"],
                        "closed_at": row.get("closed_at"),
                        "total_duration_ms": row.get("total_duration_ms"),
                        "question_preview": row.get("question_preview"),
                        "span_count": int(row.get("span_count") or 0),
                        "error_count": int(row.get("error_count") or 0),
                        "degraded": bool(row.get("degraded")),
                        "fallback_used": bool(row.get("fallback_used")),
                        "slowest_span_name": row.get("slowest_span_name"),
                        "degraded_tool_names": [],
                    }
                    for row in cur.fetchall()
                ]

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        with self._connect() as conn:
            with conn.cursor(row_factory=self._dict_row()) as cur:
                cur.execute(
                    "SELECT * FROM agent_trace_spans WHERE trace_id = %s ORDER BY start_time ASC",
                    (trace_id,),
                )
                return [_span_from_row(row) for row in cur.fetchall()]

    def delete_traces(self, trace_ids: list[str], batch_size: int = 500) -> int:
        trace_ids = [str(trace_id) for trace_id in trace_ids if trace_id]
        if not trace_ids:
            return 0
        deleted = 0
        batch_size = max(1, min(int(batch_size or 500), 1000))
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for index in range(0, len(trace_ids), batch_size):
                        batch = trace_ids[index : index + batch_size]
                        cur.execute(
                            "DELETE FROM agent_traces WHERE trace_id = ANY(%s)",
                            (batch,),
                        )
                        deleted += int(cur.rowcount or 0)
            self._record_success()
            return deleted
        except Exception as exc:
            self._record_error(exc)
            raise

    def healthcheck(self) -> bool:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    healthy = cur.fetchone() is not None
                    if healthy:
                        self._record_success()
                    return healthy
        except Exception as exc:
            self._record_error(exc)
            return False

    def health_status(self) -> RepositoryHealth:
        healthy = self.healthcheck()
        return RepositoryHealth(
            backend="postgres",
            configured_backend=self.configured_backend,
            healthy=healthy,
            degraded=self.degraded or not healthy,
            ever_degraded=self.ever_degraded,
            last_error=self.last_error,
            last_error_at=self.last_error_at,
            last_success_at=self.last_success_at,
            storage_path=None,
            database_url_configured=bool(self.database_url),
            capture_mode=resolve_capture_mode(),
        )

    def _record_success(self) -> None:
        self.last_success_at = datetime.now(timezone.utc)
        self.degraded = False
        self.last_error = None

    def _record_error(self, exc: Exception) -> None:
        self.degraded = True
        self.ever_degraded = True
        self.last_error = str(exc)[:500]
        self.last_error_at = datetime.now(timezone.utc)

    def _connect(self, *, autocommit: bool = True):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError(
                "psycopg is required for PostgreSQL trace backend"
            ) from exc
        return psycopg.connect(self.database_url, autocommit=autocommit)

    @staticmethod
    def _dict_row():
        from psycopg.rows import dict_row

        return dict_row


def build_trace_repository(storage_path: Path | None = None) -> TraceRepository:
    if storage_path is not None:
        repository: TraceRepository = JsonlTraceRepository(
            storage_path,
            configured_backend="jsonl",
        )
        repository.initialize()
        return repository

    backend = getattr(settings, "trace_backend", "auto")
    database_url = getattr(settings, "trace_database_url", None) or getattr(
        settings, "database_url", None
    )
    if backend == "jsonl":
        repository = JsonlTraceRepository(configured_backend="jsonl")
        repository.initialize()
        return repository
    if backend == "postgres":
        repository = PostgreSQLTraceRepository(str(database_url or ""), configured_backend="postgres")
        repository.initialize()
        return repository
    if database_url:
        try:
            repository = PostgreSQLTraceRepository(str(database_url), configured_backend="auto")
            repository.initialize()
            return repository
        except Exception as exc:
            logger.warning("PostgreSQL trace backend unavailable, falling back to JSONL: %s", exc)
            repository = JsonlTraceRepository(
                configured_backend="auto",
                degraded=True,
                last_error=str(exc)[:500],
            )
            repository.initialize()
            return repository
    repository = JsonlTraceRepository(configured_backend="auto" if backend == "auto" else backend)
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


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _datetime_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _walk_spans(span: TraceSpan):
    yield span
    for child in span.children:
        yield from _walk_spans(child)


def _span_flag(span: TraceSpan, key: str) -> bool:
    metadata = span.metadata if isinstance(span.metadata, dict) else {}
    outputs = span.outputs if isinstance(span.outputs, dict) else {}
    return bool(getattr(span, key, False) or metadata.get(key) or outputs.get(key))


def _trace_row(trace: Trace) -> dict[str, Any]:
    question_fields = question_persistence_fields(trace.question)
    return {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "session_id": trace.session_id,
        "user_id": trace.user_id,
        "question": question_fields["question"],
        "question_hash": question_fields["question_hash"],
        "question_preview": question_fields["question_preview"],
        "question_length": question_fields["question_length"],
        "normalized_question": _safe_text_column(trace.normalized_question),
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
        "status": _normalize_trace_status_value(trace.status),
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
        question=trace_row.get("question_preview") or trace_row.get("question") or "",
        question_hash=trace_row.get("question_hash"),
        question_preview=trace_row.get("question_preview"),
        question_length=trace_row.get("question_length"),
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


def _normalize_trace_status_value(value: Any) -> str:
    return Trace.model_validate(
        {
            "trace_id": "status-normalizer",
            "session_id": "status-normalizer",
            "question": "status-normalizer",
            "root_span": {"name": "harness", "kind": "agent"},
            "status": _enum_value(value),
        }
    ).status.value


def _normalize_span_status_value(value: Any) -> str:
    normalized = _enum_value(value).lower()
    if normalized not in {item.value for item in SpanStatus}:
        return SpanStatus.ERROR.value
    return normalized


def _safe_text_column(value: Any) -> str | None:
    if value is None:
        return None
    mode = resolve_capture_mode()
    if mode == "minimal":
        return None
    return question_persistence_fields(str(value), mode)["question_preview"]
