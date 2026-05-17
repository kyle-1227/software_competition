from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.eval_adapter import trace_to_eval_case


def test_trace_to_eval_case_outputs_standard_fields() -> None:
    trace = _trace()

    case = trace_to_eval_case(trace)

    assert case["trace_id"] == trace.trace_id
    assert case["question_hash"]
    assert case["question_preview"] == trace.question
    assert case["failure_type"] == "retrieval_failure"
    assert case["root_cause_span"]["name"] == "retriever.vector_search"
    assert case["retrieved_pages"] == [8, 9]
    assert case["evidence_count"] == 0
    assert case["confidence"] == 0.6
    assert case["suggested_fix"]


def test_trace_to_eval_case_hides_preview_in_minimal_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")

    case = trace_to_eval_case(_trace())

    assert case["question_preview"] is None
    assert case["question_hash"]


def _trace() -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    retriever = TraceSpan(
        name="retriever.vector_search",
        kind=SpanKind.RETRIEVER,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
        metadata={
            "retrieved_pages": [8, 9],
            "evidence_count": 0,
            "placeholder_used": True,
        },
    )
    evaluator = TraceSpan(
        name="evaluator.optimizer",
        kind=SpanKind.EVALUATOR,
        start_time=started + timedelta(milliseconds=20),
        end_time=started + timedelta(milliseconds=30),
        duration_ms=10,
        metadata={"final_confidence": 0.6},
    )
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        duration_ms=100,
        children=[retriever, evaluator],
    )
    return Trace(
        trace_id="trace-eval-adapter",
        session_id="session-1",
        question="why did the engine fail?",
        root_span=root,
        total_duration_ms=100,
    )
