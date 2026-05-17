from __future__ import annotations

from app.schemas.trace import SpanKind, Trace, TraceSpan, TraceStatus


def test_trace_schema_accepts_legacy_trace_payload() -> None:
    trace = Trace.model_validate(
        {
            "trace_id": "legacy-trace",
            "session_id": "",
            "question": "",
            "root_span": {"name": "harness", "kind": "agent"},
            "created_at": "2026-01-01T00:00:00Z",
        }
    )

    assert trace.trace_id == "legacy-trace"
    assert trace.status == TraceStatus.RUNNING
    assert trace.feature_flags == {}
    assert trace.question_hash is None
    assert trace.question_preview is None
    assert trace.question_length is None
    assert trace.closed_at is None
    assert trace.root_span.duration_ms is None


def test_trace_schema_round_trips_production_fields() -> None:
    span = TraceSpan(
        trace_id="trace-1",
        name="tool.manual_lookup.attempt",
        kind=SpanKind.TOOL,
        duration_ms=12.5,
        attempt=1,
        retry_count=0,
        fallback_used=True,
        degraded=True,
        token_usage={"input_tokens": 1},
        cost_estimate={"usd": 0.01},
        quality={"confidence": 0.8},
    )
    trace = Trace(
        trace_id="trace-1",
        run_id="run-1",
        session_id="session-1",
        user_id="user-1",
        question="question",
        normalized_question="question",
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span]),
        feature_flags={"agent_loop_enabled": True},
        status="ok",
        total_duration_ms=20.0,
    )

    loaded = Trace.model_validate(trace.model_dump(mode="json"))

    assert loaded.run_id == "run-1"
    assert loaded.status == TraceStatus.SUCCESS
    assert loaded.total_duration_ms == 20.0
    assert loaded.root_span.children[0].fallback_used is True
    assert loaded.root_span.children[0].quality == {"confidence": 0.8}


def test_trace_schema_maps_unknown_status_to_error() -> None:
    trace = Trace.model_validate(
        {
            "trace_id": "legacy-trace",
            "session_id": "session",
            "question": "question",
            "root_span": {"name": "harness", "kind": "agent"},
            "status": "unexpected",
        }
    )

    assert trace.status == TraceStatus.ERROR
