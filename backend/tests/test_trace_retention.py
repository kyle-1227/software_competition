from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.retention import (
    REASON_DEGRADED_OLD,
    REASON_ERROR_OLD,
    REASON_EVAL_EXPORTED_OLD,
    REASON_SUCCESS_OLD,
    TraceRetentionPolicy,
    load_eval_exported_trace_ids,
    select_cleanup_candidates,
)


def test_retention_candidate_reasons_and_safety_rules(tmp_path) -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    policy = TraceRetentionPolicy(keep_days=30, keep_error_days=90, keep_degraded_days=90)
    traces = [
        _trace("success-old", status="success", closed_at=now - timedelta(days=31)),
        _trace("error-old", status="error", closed_at=now - timedelta(days=91)),
        _trace("degraded-old", status="success", closed_at=now - timedelta(days=91), degraded=True),
        _trace("running-old", status="running", closed_at=now - timedelta(days=365)),
        _trace("open", status="success", closed_at=None),
    ]

    candidates = select_cleanup_candidates(traces, policy, now=now)
    reasons = {candidate.trace_id: candidate.reason for candidate in candidates}

    assert reasons["success-old"] == REASON_SUCCESS_OLD
    assert reasons["error-old"] == REASON_ERROR_OLD
    assert reasons["degraded-old"] == REASON_DEGRADED_OLD
    assert "running-old" not in reasons
    assert "open" not in reasons


def test_eval_exported_traces_use_eval_retention_only_when_dataset_is_provided(tmp_path) -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    policy = TraceRetentionPolicy(keep_days=30, keep_eval_exported_days=180)
    trace = _trace("eval-trace", status="success", closed_at=now - timedelta(days=120))
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps({"case_id": "c", "trace_id": "eval-trace"}) + "\n", encoding="utf-8")

    no_dataset = select_cleanup_candidates([trace], policy, now=now)
    with_dataset = select_cleanup_candidates(
        [trace],
        policy,
        now=now,
        eval_exported_trace_ids=load_eval_exported_trace_ids(dataset),
    )
    older_eval = select_cleanup_candidates(
        [_trace("eval-trace", status="success", closed_at=now - timedelta(days=181))],
        policy,
        now=now,
        eval_exported_trace_ids=load_eval_exported_trace_ids(dataset),
    )

    assert no_dataset[0].reason == REASON_SUCCESS_OLD
    assert with_dataset == []
    assert older_eval[0].reason == REASON_EVAL_EXPORTED_OLD


def test_max_delete_is_hard_bound() -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    traces = [
        _trace(f"trace-{index}", status="success", closed_at=now - timedelta(days=100))
        for index in range(5)
    ]

    candidates = select_cleanup_candidates(
        traces,
        TraceRetentionPolicy(keep_days=1, max_delete=2),
        now=now,
    )

    assert len(candidates) == 2


def _trace(
    trace_id: str,
    *,
    status: str,
    closed_at: datetime | None,
    degraded: bool = False,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name="node.work",
        kind=SpanKind.NODE,
        degraded=degraded,
        metadata={"degraded": degraded},
    )
    return Trace(
        trace_id=trace_id,
        session_id="session",
        question="q",
        status=status,
        created_at=started,
        closed_at=closed_at,
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span]),
    )
