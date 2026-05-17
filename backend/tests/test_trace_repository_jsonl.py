from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, Trace, TraceSpan, TraceStatus
from app.services.tracing.repository import JsonlTraceRepository


def test_jsonl_repository_save_get_list_and_health(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    repository.close_trace(trace)

    loaded = repository.get_trace(trace.trace_id)
    traces = repository.list_traces(limit=5, session_id=trace.session_id)
    summaries = repository.list_trace_summaries(limit=5, session_id=trace.session_id)
    spans = repository.list_spans(trace.trace_id)
    health = repository.health_status()

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id
    assert traces[0].status == TraceStatus.SUCCESS
    assert summaries[0]["span_count"] == 1
    assert summaries[0]["error_count"] == 0
    assert summaries[0]["slowest_span_name"] == "node.orchestrator"
    assert spans[0].name == "node.orchestrator"
    assert health.backend == "jsonl"
    assert health.healthy is True


def _trace() -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name="node.orchestrator",
        kind=SpanKind.NODE,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
    )
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        duration_ms=100,
        children=[span],
    )
    return Trace(
        trace_id="jsonl-repository-trace",
        session_id="jsonl-session",
        question="q",
        root_span=root,
        status="success",
        total_duration_ms=100,
    )
