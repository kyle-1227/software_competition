from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan, TraceStatus
from app.services.tracing.eval_adapter import (
    TRACE_EVAL_CASE_SCHEMA_VERSION,
    TraceEvalCase,
    should_export_trace_to_eval,
    trace_to_eval_case,
)


def test_trace_to_eval_case_outputs_production_schema() -> None:
    trace = _trace(
        _span(
            "retriever.vector_search",
            SpanKind.RETRIEVER,
            metadata={"retrieved_pages": [8, 9], "evidence_count": 0, "placeholder_used": True},
        ),
        _span("evaluator.optimizer", SpanKind.EVALUATOR, metadata={"final_confidence": 0.6}),
    )

    case = trace_to_eval_case(trace)

    assert case["schema_version"] == TRACE_EVAL_CASE_SCHEMA_VERSION
    assert case["trace_id"] == trace.trace_id
    assert case["source"] == "trace_export"
    assert case["question_hash"]
    assert case["question_preview"] != trace.question
    assert case["failure_type"] == "retrieval_failure"
    assert case["root_cause_span"]["name"] == "retriever.vector_search"
    assert case["retrieved_pages"] == [8, 9]
    assert case["evidence_count"] == 0
    assert case["confidence"] == 0.6
    assert case["suggested_fix"]
    assert case["expected_behavior"]
    assert all("type" in item and "expected" in item for item in case["assertions"])
    assert case["metadata"]["app_env"] == "test"
    assert case["metadata"]["llm_model"] == "test-llm"


def test_clean_success_trace_is_not_eligible_unless_requested() -> None:
    trace = _trace(
        _span("retriever.vector_search", SpanKind.RETRIEVER, metadata={"evidence_count": 2}),
        status=TraceStatus.SUCCESS,
    )

    assert should_export_trace_to_eval(trace) is False
    assert should_export_trace_to_eval(trace, include_success=True) is True

    case = trace_to_eval_case(trace, include_success=True)
    assert case["failure_type"] == "success"
    assert case["expected_behavior"] == "System should preserve this successful grounded behavior."


def test_degraded_success_trace_is_eligible() -> None:
    trace = _trace(
        _span(
            "node.fallback",
            SpanKind.NODE,
            metadata={"degraded": True, "fallback_used": True},
            degraded=True,
            fallback_used=True,
        )
    )

    assert should_export_trace_to_eval(trace) is True
    assert trace_to_eval_case(trace)["failure_type"] == "fallback_degraded"


def test_source_values_are_strict() -> None:
    with pytest.raises(ValidationError):
        TraceEvalCase(
            case_id="case",
            trace_id="trace",
            source="manual",
            created_at="2026-01-01T00:00:00+00:00",
            failure_type="success",
            trace_status="success",
            expected_behavior="ok",
        )


def test_case_id_uses_only_stable_fields(monkeypatch) -> None:
    trace = _trace(
        _span("tool.manual_lookup.attempt", SpanKind.TOOL, status=SpanStatus.ERROR),
        question="api_key=secret full question should not define the id " * 20,
    )

    monkeypatch.setattr(settings, "trace_capture_mode", "summary")
    first = trace_to_eval_case(trace)
    monkeypatch.setattr(settings, "trace_capture_mode", "debug")
    second = trace_to_eval_case(trace)

    assert first["case_id"] == second["case_id"]
    assert first["question_preview"] != second["question_preview"]


def test_trace_to_eval_case_redacts_sensitive_and_full_content() -> None:
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    full_answer = "FULL ANSWER SHOULD NOT LEAK " * 20
    full_script = "print('FULL SCRIPT SHOULD NOT LEAK')\n" * 20
    full_evidence = "FULL EVIDENCE SHOULD NOT LEAK " * 20
    trace = _trace(
        _span(
            "tool.manual_lookup.attempt",
            SpanKind.TOOL,
            status=SpanStatus.ERROR,
            inputs={
                "api_key": "real-api-key",
                "authorization": "Bearer real-token",
                "script": full_script,
                "reasoning": "hidden reasoning",
                "chain_of_thought": "hidden chain",
            },
            outputs={
                "answer": full_answer,
                "evidence": [{"page": 3, "snippet": full_evidence}],
            },
        ),
        question=full_question,
    )

    rendered = json.dumps(trace_to_eval_case(trace), ensure_ascii=False)

    for forbidden in (
        full_question,
        full_answer,
        full_script,
        full_evidence,
        "real-api-key",
        "real-token",
        "hidden reasoning",
        "hidden chain",
    ):
        assert forbidden not in rendered


def test_minimal_mode_hides_question_preview(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")

    case = trace_to_eval_case(_trace(_span("tool.x.attempt", SpanKind.TOOL, status=SpanStatus.ERROR)))

    assert case["question_preview"] is None
    assert case["question_hash"]


def _trace(
    *spans: TraceSpan,
    question: str = "why did the engine fail?",
    status: TraceStatus = TraceStatus.SUCCESS,
) -> Trace:
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
        trace_id="trace-eval-adapter",
        session_id="session-1",
        question=question,
        root_span=root,
        total_duration_ms=100,
        status=status,
        app_env="test",
        app_version="1.2.3",
        git_commit="abc123",
        llm_model="test-llm",
        manual_id="manual-1",
        index_version="idx-1",
    )


def _span(
    name: str,
    kind: SpanKind,
    *,
    status: SpanStatus = SpanStatus.OK,
    metadata=None,
    inputs=None,
    outputs=None,
    degraded: bool = False,
    fallback_used: bool = False,
) -> TraceSpan:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TraceSpan(
        name=name,
        kind=kind,
        status=status,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
        metadata=metadata or {},
        inputs=inputs or {},
        outputs=outputs or {},
        degraded=degraded,
        fallback_used=fallback_used,
    )
