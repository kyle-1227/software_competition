from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.repository import PostgreSQLTraceRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "true"
    or not (os.getenv("TRACE_DATABASE_URL") or os.getenv("DATABASE_URL")),
    reason="PostgreSQL trace repository tests require RUN_POSTGRES_TESTS=true and a database URL",
)


def test_postgres_repository_save_get_and_list() -> None:
    database_url = os.environ.get("TRACE_DATABASE_URL") or os.environ["DATABASE_URL"]
    repository = PostgreSQLTraceRepository(database_url)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    trace.status = "ok"
    trace.closed_at = datetime.now(timezone.utc)
    trace.total_duration_ms = 100
    repository.close_trace(trace)

    loaded = repository.get_trace(trace.trace_id)
    traces = repository.list_traces(limit=5, session_id=trace.session_id)
    spans = repository.list_spans(trace.trace_id)

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id
    assert traces
    assert spans[0].name == span.name


def _trace() -> Trace:
    started = datetime.now(timezone.utc)
    span = TraceSpan(
        trace_id="postgres-test-trace",
        name="node.orchestrator",
        kind=SpanKind.NODE,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
    )
    root = TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span])
    return Trace(
        trace_id="postgres-test-trace",
        session_id="postgres-test-session",
        question="q",
        root_span=root,
        created_at=started,
    )
