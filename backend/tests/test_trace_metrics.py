from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas.trace import SpanKind, Trace, TraceSpan, TraceStatus
from app.services.tracing.analytics import FailureType
from app.services.tracing.metrics import (
    build_trace_eval_readiness_metrics,
    build_trace_failure_metrics,
    build_trace_latency_metrics,
    build_trace_metrics_overview,
    build_trace_operational_metrics,
    build_trace_repository_metrics,
)


def test_overview_counts_and_rates() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("s1", status="success", closed_at=now),
        _trace("s2", status="success", closed_at=now),
        _trace("e1", status="error", closed_at=now),
        _trace("r1", status="running", closed_at=now),
        _trace("c1", status="cancelled", closed_at=now),
        _trace("d1", status="success", closed_at=now, degraded=True),
        _trace("f1", status="success", closed_at=now, fallback_used=True),
        _trace("su1", status="SUCCESS", closed_at=now),
    ]

    overview = build_trace_metrics_overview(traces, window_hours=24)

    assert overview["total_traces"] == 8
    assert overview["status_counts"] == {"success": 5, "error": 1, "running": 1, "cancelled": 1}
    assert overview["success_count"] == 5
    assert overview["error_count"] == 1
    assert overview["running_count"] == 1
    assert overview["cancelled_count"] == 1
    assert overview["degraded_count"] == 1
    assert overview["fallback_count"] == 1
    assert overview["failure_rate"] == round(1 / 8, 4)
    assert overview["degraded_rate"] == round(1 / 8, 4)
    assert overview["fallback_rate"] == round(1 / 8, 4)


def test_overview_empty_traces() -> None:
    overview = build_trace_metrics_overview([], window_hours=24)

    assert overview["total_traces"] == 0
    assert overview["status_counts"] == {"success": 0, "error": 0, "running": 0, "cancelled": 0}
    assert overview["failure_rate"] == 0.0
    assert overview["degraded_rate"] == 0.0
    assert overview["fallback_rate"] == 0.0


def test_failure_metrics_counts() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("ret-1", status="error", closed_at=now, span_name="retriever.vector_search"),
        _trace("ret-2", status="error", closed_at=now, span_name="retriever.vector_search"),
        _trace("tool-1", status="error", closed_at=now, span_name="tool.manual_lookup.attempt"),
        _trace("success-1", status="success", closed_at=now),
    ]

    metrics = build_trace_failure_metrics(traces, top_n=10)

    failure_types = {item["failure_type"]: item["count"] for item in metrics["failure_type_counts"]}
    assert failure_types.get("retrieval_failure", 0) >= 2
    assert "success" not in failure_types
    assert metrics["top_n"] == 10


def test_failure_metrics_excludes_success() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("ok", status="success", closed_at=now),
    ]

    metrics = build_trace_failure_metrics(traces, top_n=10)

    assert metrics["failure_type_counts"] == []
    assert metrics["root_cause_span_counts"] == []


def test_latency_metrics_percentiles() -> None:
    now = datetime.now(timezone.utc)
    traces = []
    for i, dur in enumerate([100, 200, 300, 1000, 10000]):
        traces.append(_trace(f"trace-{i}", status="success", closed_at=now, duration_ms=float(dur)))

    metrics = build_trace_latency_metrics(traces, slow_threshold_ms=5000)

    assert metrics["count"] == 5
    assert metrics["duration_available_count"] == 5
    assert metrics["p50_ms"] == 300
    assert metrics["p95_ms"] == 10000
    assert metrics["p99_ms"] == 10000
    assert metrics["max_ms"] == 10000
    assert metrics["slow_trace_count"] == 1
    assert metrics["slow_trace_rate"] == 0.2


def test_latency_metrics_missing_duration() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("t1", status="success", closed_at=now, duration_ms=None),
    ]

    metrics = build_trace_latency_metrics(traces, slow_threshold_ms=5000)

    assert metrics["duration_available_count"] == 0
    assert metrics["p50_ms"] is None
    assert metrics["slow_trace_count"] == 0
    assert metrics["slow_trace_rate"] == 0.0


def test_repository_metrics() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("repo-fail", status="error", closed_at=now, span_name="trace.repository.save_span"),
    ]
    health = {
        "backend": "postgres",
        "configured_backend": "postgres",
        "healthy": True,
        "degraded": False,
        "ever_degraded": True,
        "last_error": None,
        "last_error_at": None,
        "last_success_at": now.isoformat(),
        "storage_path": "/tmp/sensitive/path",
        "database_url_configured": True,
        "capture_mode": "summary",
        "unknown_extra_key": "should be stripped",
    }

    metrics = build_trace_repository_metrics(traces, repository_health=health)

    assert metrics["health"]["backend"] == "postgres"
    assert metrics["health"]["storage_path_configured"] is True
    assert "unknown_extra_key" not in metrics["health"]
    assert "storage_path" not in metrics["health"]
    assert metrics["repository_failure_count"] >= 1


def test_eval_readiness_metrics(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("eligible-1", status="error", closed_at=now, span_name="retriever.vector_search"),
    ]
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps({"case_id": "c1", "trace_id": "eligible-1"}) + "\n", encoding="utf-8")

    metrics = build_trace_eval_readiness_metrics(traces, eval_dataset_path=dataset)

    assert metrics["eligible_eval_cases"] == 1
    assert metrics["exported_eval_cases"] == 1
    assert metrics["deduplicated_trace_ids"] == 1
    assert metrics["unexported_eligible_eval_cases"] == 0
    assert metrics["export_coverage_rate"] == 1.0


def test_eval_readiness_missing_dataset() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("eligible-1", status="error", closed_at=now, span_name="retriever.vector_search"),
    ]

    metrics = build_trace_eval_readiness_metrics(traces, eval_dataset_path=Path("/nonexistent/eval.jsonl"))

    assert metrics["exported_eval_cases"] == 0
    assert metrics["deduplicated_trace_ids"] == 0
    assert metrics["export_coverage_rate"] == 0.0


def test_metrics_no_sensitive_leaks() -> None:
    now = datetime.now(timezone.utc)
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    trace = Trace(
        trace_id="sensitive-test",
        session_id="session",
        question=full_question,
        status="success",
        created_at=now,
        closed_at=now,
        total_duration_ms=100,
        root_span=TraceSpan(
            name="harness",
            kind=SpanKind.AGENT,
            children=[
                TraceSpan(
                    name="llm.answer_generation",
                    kind=SpanKind.LLM,
                    inputs={"api_key": "real-api-key", "token": "secret-token"},
                    outputs={
                        "answer": "FULL ANSWER SHOULD NOT LEAK " * 20,
                        "chain_of_thought": "private reasoning",
                    },
                )
            ],
        ),
    )
    traces = [trace]

    payload = build_trace_operational_metrics(
        traces,
        repository_health={"backend": "jsonl", "healthy": True},
        window_hours=24,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "FULL QUESTION" not in rendered
    assert "FULL ANSWER" not in rendered
    assert "real-api-key" not in rendered
    assert "secret-token" not in rendered
    assert "private reasoning" not in rendered
    assert "chain_of_thought" not in rendered


def test_clamp_metrics_params() -> None:
    from app.services.tracing.metrics import load_recent_traces_for_metrics

    class _FakeStore:
        def list_trace_summaries(self, limit=50, session_id=None, status=None):
            return []

    store = _FakeStore()

    r1 = load_recent_traces_for_metrics(store, window_hours=1000, limit=10000)
    assert len(r1.traces) == 0

    r2 = load_recent_traces_for_metrics(store, window_hours=0, limit=0)
    assert len(r2.traces) == 0


def test_latency_metrics_allows_zero_threshold() -> None:
    now = datetime.now(timezone.utc)
    traces = [_trace("t1", status="success", closed_at=now, duration_ms=100.0)]

    metrics = build_trace_latency_metrics(traces, slow_threshold_ms=0)

    assert metrics["slow_threshold_ms"] == 0
    assert metrics["slow_trace_count"] == 1
    assert metrics["slow_trace_rate"] == 1.0


def test_overview_empty_evidence_count_uses_per_trace_flags() -> None:
    now = datetime.now(timezone.utc)
    traces = [
        _trace("degraded", status="success", closed_at=now, degraded=True),
        _trace("clean-empty", status="success", closed_at=now),
    ]

    overview = build_trace_metrics_overview(traces, window_hours=24)

    assert overview["degraded_count"] == 1
    assert overview["empty_evidence_count"] == 1


def _trace(
    trace_id: str,
    *,
    status: str = "success",
    closed_at: datetime | None = None,
    degraded: bool = False,
    fallback_used: bool = False,
    span_name: str = "node.work",
    duration_ms: float | None = None,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name=span_name,
        kind=SpanKind.NODE,
        degraded=degraded,
        fallback_used=fallback_used,
        metadata={"degraded": degraded, "fallback_used": fallback_used},
        duration_ms=duration_ms,
    )
    root = TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span], duration_ms=duration_ms)
    return Trace(
        trace_id=trace_id,
        session_id="session",
        question="q",
        status=status,
        created_at=started,
        closed_at=closed_at,
        total_duration_ms=duration_ms,
        root_span=root,
    )
