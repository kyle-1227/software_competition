from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.retention import (
    REASON_DEGRADED_OLD,
    REASON_ERROR_OLD,
    REASON_EVAL_EXPORTED_OLD,
    REASON_SUCCESS_OLD,
    TraceCleanupStats,
    TraceExportStats,
    TraceRetentionPolicy,
    cleanup_candidate_for_row,
    cleanup_candidate_for_trace,
    load_eval_exported_trace_ids,
    select_cleanup_candidates,
    select_cleanup_candidates_from_rows,
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


def test_policy_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="keep_days"):
        TraceRetentionPolicy(keep_days=-1)
    with pytest.raises(ValueError, match="max_delete"):
        TraceRetentionPolicy(max_delete=-5)
    with pytest.raises(ValueError, match="batch_size"):
        TraceRetentionPolicy(batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        TraceRetentionPolicy(batch_size=-1)


def test_fallback_used_traces_use_degraded_retention() -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    policy = TraceRetentionPolicy(keep_days=30, keep_degraded_days=90)

    not_old_enough = _trace(
        "fallback-not-old",
        status="success",
        closed_at=now - timedelta(days=60),
        fallback_used=True,
    )
    old_enough = _trace(
        "fallback-old",
        status="success",
        closed_at=now - timedelta(days=120),
        fallback_used=True,
    )

    candidates = select_cleanup_candidates(
        [not_old_enough, old_enough], policy, now=now
    )

    ids = {c.trace_id for c in candidates}
    assert "fallback-not-old" not in ids, (
        "fallback trace should not be candidate before keep_degraded_days"
    )
    old = next(c for c in candidates if c.trace_id == "fallback-old")
    assert old.reason == REASON_DEGRADED_OLD
    assert old.fallback_used is True


def test_candidate_fields_reflect_trace_state() -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    policy = TraceRetentionPolicy(keep_days=1, keep_degraded_days=1, keep_eval_exported_days=180)

    degraded = cleanup_candidate_for_trace(
        _trace("d", status="success", closed_at=now - timedelta(days=10), degraded=True),
        policy,
        now=now,
    )
    fallback = cleanup_candidate_for_trace(
        _trace("f", status="success", closed_at=now - timedelta(days=10), fallback_used=True),
        policy,
        now=now,
    )
    evaled = cleanup_candidate_for_trace(
        _trace("e", status="success", closed_at=now - timedelta(days=200)),
        policy,
        eval_exported=True,
        now=now,
    )

    assert degraded is not None
    assert degraded.degraded is True
    assert degraded.fallback_used is False
    assert degraded.eval_exported is False
    assert degraded.status == "success"
    assert degraded.closed_at.endswith("+00:00")

    assert fallback is not None
    assert fallback.fallback_used is True
    assert fallback.degraded is False

    assert evaled is not None
    assert evaled.eval_exported is True


def test_cleanup_stats_to_dict_includes_archive_and_backup_paths() -> None:
    stats = TraceCleanupStats(archive_path="a.jsonl", backup_path="b.bak")
    payload = stats.to_dict()

    assert payload["archive_path"] == "a.jsonl"
    assert payload["backup_path"] == "b.bak"


def test_cleanup_stats_includes_fatal() -> None:
    stats = TraceCleanupStats(fatal=True)
    payload = stats.to_dict()

    assert payload["fatal"] is True


def test_export_stats_includes_output_path_and_fatal() -> None:
    stats = TraceExportStats(output_path="out.jsonl", fatal=True)
    payload = stats.to_dict()

    assert payload["output_path"] == "out.jsonl"
    assert payload["fatal"] is True


def test_cleanup_candidate_for_row_matches_trace_rules() -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    policy = TraceRetentionPolicy(keep_days=30, keep_error_days=90, keep_degraded_days=90, keep_eval_exported_days=180)
    closed = (now - timedelta(days=100)).isoformat()

    success = cleanup_candidate_for_row(
        {"trace_id": "s", "status": "success", "closed_at": closed}, policy, now=now
    )
    error = cleanup_candidate_for_row(
        {"trace_id": "e", "status": "error", "closed_at": closed}, policy, now=now
    )
    degraded = cleanup_candidate_for_row(
        {"trace_id": "d", "status": "success", "closed_at": closed, "degraded": True}, policy, now=now
    )
    fallback = cleanup_candidate_for_row(
        {"trace_id": "f", "status": "success", "closed_at": closed, "fallback_used": True}, policy, now=now
    )
    evaled = cleanup_candidate_for_row(
        {"trace_id": "eval", "status": "success", "closed_at": (now - timedelta(days=200)).isoformat()}, policy, eval_exported=True, now=now
    )
    running = cleanup_candidate_for_row(
        {"trace_id": "r", "status": "running", "closed_at": closed}, policy, now=now
    )
    open_trace = cleanup_candidate_for_row(
        {"trace_id": "o", "status": "success", "closed_at": None}, policy, now=now
    )

    assert success is not None and success.reason == REASON_SUCCESS_OLD
    assert error is not None and error.reason == REASON_ERROR_OLD
    assert degraded is not None and degraded.reason == REASON_DEGRADED_OLD and degraded.degraded is True
    assert fallback is not None and fallback.reason == REASON_DEGRADED_OLD and fallback.fallback_used is True
    assert evaled is not None and evaled.reason == REASON_EVAL_EXPORTED_OLD
    assert running is None
    assert open_trace is None


def test_select_cleanup_candidates_from_rows_respects_max_delete() -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    closed = (now - timedelta(days=100)).isoformat()
    rows = [
        {"trace_id": f"trace-{i}", "status": "success", "closed_at": closed}
        for i in range(5)
    ]

    candidates = select_cleanup_candidates_from_rows(
        rows, TraceRetentionPolicy(keep_days=1, max_delete=2), now=now
    )

    assert len(candidates) == 2


def _trace(
    trace_id: str,
    *,
    status: str,
    closed_at: datetime | None,
    degraded: bool = False,
    fallback_used: bool = False,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name="node.work",
        kind=SpanKind.NODE,
        degraded=degraded,
        fallback_used=fallback_used,
        metadata={"degraded": degraded, "fallback_used": fallback_used},
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
