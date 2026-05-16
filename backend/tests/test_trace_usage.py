from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.evals.metrics import build_comparison_report
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan
from app.services.tracing.analysis import analyze_eval_case_trace
from app.services.tracing.summary import build_trace_summary
from app.services.tracing.timeline import build_trace_timeline


def test_build_trace_summary_empty_trace() -> None:
    summary = build_trace_summary(None)

    assert summary["span_count"] == 0
    assert summary["error_count"] == 0
    assert summary["retrieval"]["retrieved_pages"] == []


def test_build_trace_summary_extracts_key_signals() -> None:
    trace = _trace(
        [
            _span(
                "tool.manual_lookup.attempt",
                SpanKind.TOOL,
                metadata={"attempt": 1, "max_retries": 5, "duration_ms": 30},
            ),
            _span(
                "retriever.vector_search",
                SpanKind.RETRIEVER,
                metadata={
                    "retrieved_pages": [3, 5],
                    "evidence_count": 2,
                    "placeholder_used": False,
                    "duration_ms": 95,
                },
            ),
            _span(
                "reranker.score",
                SpanKind.RERANKER,
                metadata={"candidate_count": 4, "duration_ms": 12},
            ),
            _span(
                "llm.answer_generation",
                SpanKind.LLM,
                metadata={
                    "llm_model": "local-diagnostic-template",
                    "fallback_used": True,
                    "local_fallback": True,
                    "answer_length": 128,
                    "duration_ms": 1200,
                },
            ),
            _span(
                "evaluator.optimizer",
                SpanKind.EVALUATOR,
                metadata={
                    "final_confidence": 0.86,
                    "iteration_count": 2,
                    "issues_count": 1,
                    "compliance_attempts": 1,
                    "compliance_success": True,
                    "compliance_degraded": False,
                    "duration_ms": 320,
                },
            ),
            _span("node.approval", SpanKind.NODE, metadata={"duration_ms": 5}),
        ]
    )

    summary = build_trace_summary(trace)

    assert summary["span_count"] == 6
    assert summary["tool_attempt_count"] == 1
    assert summary["retrieval"]["retrieved_pages"] == [3, 5]
    assert summary["retrieval"]["reranker_used"] is True
    assert summary["llm"]["local_fallback"] is True
    assert summary["evaluator"]["confidence"] == 0.86
    assert summary["agent_loop"]["approval_triggered"] is True
    assert summary["safety"]["approval_triggered"] is True


def test_build_trace_summary_slowest_spans() -> None:
    trace = _trace(
        [
            _span(f"node.step_{index}", SpanKind.NODE, metadata={"duration_ms": index})
            for index in range(1, 8)
        ]
    )

    slowest = build_trace_summary(trace)["slowest_spans"]

    assert len(slowest) == 5
    assert [item["duration_ms"] for item in slowest] == [7.0, 6.0, 5.0, 4.0, 3.0]


def test_build_trace_timeline_markdown() -> None:
    full_answer = "FULL ANSWER SHOULD NOT APPEAR " * 20
    full_script = "print('FULL SCRIPT SHOULD NOT APPEAR')\n" * 20
    trace = _trace(
        [
            _span("node.orchestrator", SpanKind.NODE, metadata={"intent": "fault_triage"}),
            _span(
                "tool.manual_lookup.attempt",
                SpanKind.TOOL,
                metadata={"attempt": 1, "max_retries": 5, "success": True},
            ),
            _span(
                "evaluator.optimizer",
                SpanKind.EVALUATOR,
                metadata={
                    "final_confidence": 0.8,
                    "answer": full_answer,
                    "script": full_script,
                    "reasoning": "hidden reasoning",
                },
            ),
        ]
    )

    markdown = build_trace_timeline(trace)

    assert "# Trace Timeline" in markdown
    assert "node.orchestrator" in markdown
    assert "tool.manual_lookup.attempt" in markdown
    assert "evaluator.optimizer" in markdown
    assert full_answer not in markdown
    assert full_script not in markdown
    assert "hidden reasoning" not in markdown


def test_analyze_eval_case_trace_retrieval_miss() -> None:
    analysis = analyze_eval_case_trace(
        {"id": "case-1", "expected_pages": [3], "retrieved_pages": [8, 9]},
        {"retrieval": {"retrieved_pages": [8, 9]}},
    )

    assert analysis["likely_root_cause"] == "retrieval_miss"


def test_analyze_eval_case_trace_tool_degraded() -> None:
    analysis = analyze_eval_case_trace(
        {"id": "case-1", "expected_pages": [], "retrieved_pages": []},
        {"degraded_tool_names": ["manual_lookup"]},
    )

    assert analysis["likely_root_cause"] == "tool_degradation"


def test_eval_comparison_report_includes_failed_case_trace_analysis() -> None:
    old = {"cases": []}
    new = {
        "cases": [
            {
                "id": "case-1",
                "question": "q",
                "expected_pages": [3],
                "retrieved_pages": [8, 9],
                "expected_terms": ["spark"],
                "matched_terms": [],
                "placeholder_used": False,
                "latency_ms": 10,
                "trace_id": "trace-1",
                "trace_summary": {
                    "retrieval": {"retrieved_pages": [8, 9]},
                    "evaluator": {"confidence": 0.6},
                },
            }
        ]
    }

    report = build_comparison_report(old, new)

    assert "Failed Case Trace Analysis" in report
    assert "likely root cause: retrieval_miss" in report
    assert "recommended action" in report
    assert "trace-1" in report


def test_trace_usage_does_not_leak_sensitive_or_full_content() -> None:
    full_answer = "ANSWER_BODY_SHOULD_NOT_LEAK " * 20
    full_script = "print('SCRIPT_BODY_SHOULD_NOT_LEAK')\n" * 20
    trace = _trace(
        [
            _span(
                "tool.manual_lookup.attempt",
                SpanKind.TOOL,
                inputs={
                    "api_key": "real-api-key",
                    "access_token": "secret-token",
                    "password": "real-password",
                    "secret": "real-secret",
                    "reasoning": "hidden reasoning",
                    "script": full_script,
                },
                metadata={
                    "tool_name": "manual_lookup",
                    "attempt": 1,
                    "max_retries": 5,
                    "success": False,
                    "answer": full_answer,
                    "reasoning_content": "private reasoning",
                },
            ),
            _span(
                "llm.answer_generation",
                SpanKind.LLM,
                outputs={
                    "answer": full_answer,
                    "script": full_script,
                    "thinking": "private thinking",
                },
                metadata={
                    "llm_model": "local-diagnostic-template",
                    "fallback_used": True,
                    "local_fallback": True,
                    "answer_length": len(full_answer),
                },
            ),
        ],
        question="api_key=real-api-key secret=real-secret",
    )

    rendered = json.dumps(build_trace_summary(trace), ensure_ascii=False)
    rendered += "\n" + build_trace_timeline(trace)

    assert "real-api-key" not in rendered
    assert "secret-token" not in rendered
    assert "real-password" not in rendered
    assert "real-secret" not in rendered
    assert "hidden reasoning" not in rendered
    assert "private reasoning" not in rendered
    assert "private thinking" not in rendered
    assert full_answer not in rendered
    assert full_script not in rendered


def _trace(spans: list[TraceSpan], *, question: str = "question") -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=2000),
    )
    for index, span in enumerate(spans):
        span.start_time = started + timedelta(milliseconds=index * 10)
        if span.end_time is None:
            duration = float(span.metadata.get("duration_ms") or 1)
            span.end_time = span.start_time + timedelta(milliseconds=duration)
        root.children.append(span)
    return Trace(
        trace_id="trace-usage",
        session_id="session-1",
        question=question,
        root_span=root,
    )


def _span(
    name: str,
    kind: SpanKind,
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: SpanStatus = SpanStatus.OK,
) -> TraceSpan:
    return TraceSpan(
        name=name,
        kind=kind,
        inputs=inputs or {},
        outputs=outputs or {},
        metadata=metadata or {},
        status=status,
    )
