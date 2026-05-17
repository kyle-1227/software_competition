from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan
from app.services.tracing.analytics import build_trace_analytics, classify_failure


def test_trace_analytics_classifies_retrieval_failure() -> None:
    trace = _trace(
        _span(
            "retriever.vector_search",
            SpanKind.RETRIEVER,
            outputs={"evidence_count": 0, "placeholder_used": True},
        )
    )

    result = classify_failure(trace)

    assert result["failure_type"] == "retrieval_failure"
    assert result["root_cause_span"]["name"] == "retriever.vector_search"


def test_trace_analytics_classifies_tool_failure() -> None:
    trace = _trace(_span("tool.manual_lookup.attempt", SpanKind.TOOL, status=SpanStatus.ERROR))

    assert classify_failure(trace)["failure_type"] == "tool_failure"


def test_trace_analytics_classifies_llm_failure() -> None:
    trace = _trace(
        _span(
            "llm.answer_generation",
            SpanKind.LLM,
            metadata={"local_fallback": True},
        )
    )

    assert classify_failure(trace)["failure_type"] == "llm_failure"


def test_trace_analytics_builds_bottleneck_and_flags() -> None:
    trace = _trace(
        _span("node.fast", SpanKind.NODE, duration_ms=10),
        _span("node.slow", SpanKind.NODE, duration_ms=50, metadata={"degraded": True}),
    )

    result = build_trace_analytics(trace)

    assert result["bottleneck_span"]["name"] == "node.slow"
    assert result["degraded"] is True


def _trace(*spans: TraceSpan) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        duration_ms=100,
        children=list(spans),
    )
    return Trace(
        trace_id="trace-analytics",
        session_id="session-1",
        question="q",
        root_span=root,
        total_duration_ms=100,
        status="ok",
    )


def _span(
    name: str,
    kind: SpanKind,
    *,
    status: SpanStatus = SpanStatus.OK,
    metadata=None,
    outputs=None,
    duration_ms: float = 10,
) -> TraceSpan:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TraceSpan(
        name=name,
        kind=kind,
        status=status,
        start_time=started,
        end_time=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        metadata=metadata or {},
        outputs=outputs or {},
    )
