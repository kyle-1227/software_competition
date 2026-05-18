from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import settings
from app.schemas.trace import SpanKind, Trace, TraceSpan, TraceStatus
from app.services.tracing.migrations import TRACE_MIGRATIONS
from app.services.tracing.repository import PostgreSQLTraceRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "true"
    or not (os.getenv("TRACE_DATABASE_URL") or os.getenv("DATABASE_URL")),
    reason="PostgreSQL trace repository tests require RUN_POSTGRES_TESTS=true and a database URL",
)


def test_postgres_repository_save_get_list_and_health() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    trace.status = TraceStatus.SUCCESS
    trace.closed_at = datetime.now(timezone.utc)
    trace.total_duration_ms = 100
    repository.close_trace(trace)

    loaded = repository.get_trace(trace.trace_id)
    traces = repository.list_traces(limit=5, session_id=trace.session_id)
    summaries = repository.list_trace_summaries(limit=5, session_id=trace.session_id)
    spans = repository.list_spans(trace.trace_id)
    health = repository.health_status()

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id
    assert loaded.question_hash
    assert loaded.question_preview == trace.question
    assert loaded.question_length == len(trace.question)
    assert traces
    assert spans[0].name == span.name
    assert summaries[0]["span_count"] >= 1
    assert summaries[0]["slowest_span_name"] == span.name
    assert summaries[0]["question_preview"] == trace.question
    assert health.backend == "postgres"
    assert health.healthy is True
    assert health.degraded is False
    assert health.ever_degraded is False
    assert health.last_success_at is not None


def test_postgres_repository_minimal_question_preview_is_null(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()

    repository.save_trace(trace)
    loaded = repository.get_trace(trace.trace_id)
    summaries = repository.list_trace_summaries(limit=5, session_id=trace.session_id)

    assert loaded is not None
    assert loaded.question_hash
    assert loaded.question_preview is None
    assert loaded.question_length == len(trace.question)
    assert summaries[0]["question_preview"] is None


def test_postgres_repository_migrations_are_recorded_and_idempotent() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    repository.initialize()

    with repository._connect() as conn:
        with conn.cursor(row_factory=repository._dict_row()) as cur:
            cur.execute("SELECT version, name, checksum FROM trace_schema_migrations ORDER BY version")
            rows = list(cur.fetchall())
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'agent_traces'
                  AND column_name IN ('question_hash', 'question_preview', 'question_length')
                """
            )
            question_columns = {row["column_name"] for row in cur.fetchall()}

    records = {row["version"]: row for row in rows}
    expected = {migration.version: migration for migration in TRACE_MIGRATIONS}
    assert set(records) == set(expected)
    for version, migration in expected.items():
        assert records[version]["name"] == migration.name
        assert records[version]["checksum"] == migration.checksum
    assert question_columns == {"question_hash", "question_preview", "question_length"}


def test_postgres_repository_status_constraints_reject_invalid_values() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    repository.save_trace(trace)

    with repository._connect() as conn:
        with conn.cursor() as cur:
            with pytest.raises(Exception):
                cur.execute(
                    """
                    INSERT INTO agent_traces(trace_id, session_id, question, status, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        f"invalid-status-{uuid4().hex}",
                        "session",
                        "question",
                        "invalid",
                        datetime.now(timezone.utc),
                    ),
                )
            with pytest.raises(Exception):
                cur.execute(
                    """
                    INSERT INTO agent_trace_spans(
                        span_id, trace_id, name, kind, status, start_time
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f"invalid-span-{uuid4().hex}",
                        trace.trace_id,
                        "node.invalid",
                        "node",
                        "invalid",
                        datetime.now(timezone.utc),
                    ),
                )


def test_postgres_list_trace_cleanup_rows() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]
    span.degraded = True
    span.fallback_used = True

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    trace.status = TraceStatus.SUCCESS
    trace.closed_at = datetime.now(timezone.utc)
    trace.total_duration_ms = 100
    repository.close_trace(trace)

    rows = repository.list_trace_cleanup_rows(limit=100)

    matching = [r for r in rows if r["trace_id"] == trace.trace_id]
    assert matching
    row = matching[0]
    assert row["trace_id"] == trace.trace_id
    assert row["status"] in ("success", "SUCCESS")
    assert row["closed_at"] is not None
    assert row["degraded"] is True
    assert row["fallback_used"] is True
    assert "question" not in row
    assert "answer" not in row


def test_postgres_delete_traces_batch_deletes_and_cascades_spans() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    trace.status = TraceStatus.SUCCESS
    trace.closed_at = datetime.now(timezone.utc)
    trace.total_duration_ms = 100
    repository.close_trace(trace)

    deleted = repository.delete_traces([trace.trace_id], batch_size=1)

    assert deleted == 1
    assert repository.get_trace(trace.trace_id) is None
    assert repository.list_spans(trace.trace_id) == []


def test_postgres_delete_traces_does_not_delete_open_trace() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)

    deleted = repository.delete_traces([trace.trace_id], batch_size=1)

    assert deleted == 0
    assert repository.get_trace(trace.trace_id) is not None


def test_postgres_cleanup_service_apply_deletes_candidates() -> None:
    from app.services.tracing.retention import TraceRetentionPolicy, cleanup_postgres_traces

    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    trace.status = TraceStatus.SUCCESS
    trace.closed_at = datetime.now(timezone.utc) - timedelta(days=60)
    trace.total_duration_ms = 100
    repository.close_trace(trace)

    stats = cleanup_postgres_traces(
        repository,
        policy=TraceRetentionPolicy(keep_days=30, archive_before_delete=False),
        apply=True,
    )

    assert stats.deleted == 1
    assert repository.get_trace(trace.trace_id) is None
    assert repository.list_spans(trace.trace_id) == []


def _trace() -> Trace:
    started = datetime.now(timezone.utc)
    span = TraceSpan(
        trace_id=f"postgres-test-trace-{uuid4().hex}",
        name="node.orchestrator",
        kind=SpanKind.NODE,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
    )
    root = TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span])
    return Trace(
        trace_id=span.trace_id or f"postgres-test-trace-{uuid4().hex}",
        session_id=f"postgres-test-session-{uuid4().hex}",
        question="q",
        root_span=root,
        created_at=started,
    )
