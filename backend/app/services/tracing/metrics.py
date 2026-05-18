from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from typing import Any, TextIO

from app.schemas.trace import Trace
from app.services.tracing.analytics import (
    FailureType,
    build_trace_analytics,
)
from app.services.tracing.eval_adapter import should_export_trace_to_eval
from app.services.tracing.eval_dataset import default_trace_eval_dataset_path
from app.services.tracing.reader import find_trace_by_id
from app.services.tracing.summary import build_trace_summary

SCHEMA_VERSION = "trace_operational_metrics.v1"
ALLOWED_HEALTH_KEYS = {
    "backend",
    "configured_backend",
    "healthy",
    "degraded",
    "ever_degraded",
    "last_error",
    "last_error_at",
    "last_success_at",
    "database_url_configured",
    "capture_mode",
}


@dataclass
class TraceMetricsLoadResult:
    traces: list[Trace] = field(default_factory=list)
    skipped_trace_count: int = 0


def load_recent_traces_for_metrics(
    trace_store: Any,
    *,
    window_hours: int = 24,
    limit: int = 1000,
    session_id: str | None = None,
    status: str | None = None,
) -> TraceMetricsLoadResult:
    window_hours = max(1, min(int(window_hours or 24), 720))
    limit = max(1, min(int(limit or 1000), 5000))

    try:
        summaries = trace_store.list_trace_summaries(limit=limit, session_id=session_id, status=status)
    except Exception:
        return TraceMetricsLoadResult()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    traces: list[Trace] = []
    skipped = 0

    for summary in summaries:
        try:
            trace = find_trace_by_id(trace_store, summary["trace_id"])
        except Exception:
            skipped += 1
            continue
        if trace is None:
            skipped += 1
            continue
        ts = trace.closed_at or trace.created_at
        if ts is None:
            skipped += 1
            continue
        if ts < cutoff:
            skipped += 1
            continue
        traces.append(trace)

    return TraceMetricsLoadResult(traces=traces, skipped_trace_count=skipped)


def build_trace_metrics_overview(
    traces: list[Trace],
    *,
    window_hours: int = 24,
) -> dict[str, Any]:
    total = len(traces)
    status_counts = {"success": 0, "error": 0, "running": 0, "cancelled": 0}
    degraded = 0
    fallback = 0
    repository_failure = 0
    guardrail_blocked = 0
    approval_required = 0
    low_confidence = 0
    empty_evidence = 0

    for trace in traces:
        st = _norm_status(trace.status)
        if st in status_counts:
            status_counts[st] += 1
        analytics = build_trace_analytics(trace)
        if analytics.get("degraded"):
            degraded += 1
        if analytics.get("fallback_used"):
            fallback += 1
        ft = str(analytics.get("failure_type") or "")
        if ft == FailureType.TRACE_REPOSITORY_FAILURE.value:
            repository_failure += 1
        elif ft == FailureType.GUARDRAIL_BLOCKED.value:
            guardrail_blocked += 1
        elif ft == FailureType.POLICY_APPROVAL_REQUIRED.value:
            approval_required += 1
        elif ft == FailureType.EVALUATOR_LOW_CONFIDENCE.value:
            low_confidence += 1
        summary = build_trace_summary(trace)
        evidence_count = int((summary.get("retrieval") or {}).get("evidence_count") or 0)
        if evidence_count == 0 and not (degraded or fallback):
            empty_evidence += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "window_hours": window_hours,
        "total_traces": total,
        "status_counts": status_counts,
        "success_count": status_counts["success"],
        "error_count": status_counts["error"],
        "running_count": status_counts["running"],
        "cancelled_count": status_counts["cancelled"],
        "degraded_count": degraded,
        "fallback_count": fallback,
        "repository_failure_count": repository_failure,
        "guardrail_blocked_count": guardrail_blocked,
        "approval_required_count": approval_required,
        "low_confidence_count": low_confidence,
        "empty_evidence_count": empty_evidence,
        "failure_rate": round(status_counts["error"] / total, 4) if total else 0.0,
        "degraded_rate": round(degraded / total, 4) if total else 0.0,
        "fallback_rate": round(fallback / total, 4) if total else 0.0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_trace_failure_metrics(
    traces: list[Trace],
    *,
    top_n: int = 10,
) -> dict[str, Any]:
    top_n = max(1, min(int(top_n or 10), 50))
    failure_type_counts: dict[str, int] = {}
    root_cause_counts: dict[str, int] = {}
    guardrail = policy = sandbox = tool = retrieval = llm = repository = unknown = 0

    for trace in traces:
        analytics = build_trace_analytics(trace)
        ft = str(analytics.get("failure_type") or "")
        if ft == FailureType.SUCCESS.value:
            continue
        failure_type_counts[ft] = failure_type_counts.get(ft, 0) + 1
        root_cause = analytics.get("root_cause_span")
        if isinstance(root_cause, dict):
            name = str(root_cause.get("name") or "")
            if name:
                root_cause_counts[name] = root_cause_counts.get(name, 0) + 1

        if ft == FailureType.GUARDRAIL_BLOCKED.value:
            guardrail += 1
        elif ft == FailureType.POLICY_APPROVAL_REQUIRED.value:
            policy += 1
        elif ft == FailureType.SANDBOX_REJECTED.value:
            sandbox += 1
        elif ft == FailureType.TOOL_FAILURE.value:
            tool += 1
        elif ft == FailureType.RETRIEVAL_FAILURE.value:
            retrieval += 1
        elif ft == FailureType.LLM_FAILURE.value:
            llm += 1
        elif ft == FailureType.TRACE_REPOSITORY_FAILURE.value:
            repository += 1
        elif ft == FailureType.UNKNOWN_FAILURE.value:
            unknown += 1

    sorted_types = sorted(failure_type_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    sorted_roots = sorted(root_cause_counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]

    return {
        "failure_type_counts": [{"failure_type": k, "count": v} for k, v in sorted_types],
        "root_cause_span_counts": [{"span": k, "count": v} for k, v in sorted_roots],
        "guardrail_blocked_count": guardrail,
        "policy_failure_count": policy,
        "sandbox_failure_count": sandbox,
        "tool_failure_count": tool,
        "retrieval_failure_count": retrieval,
        "llm_failure_count": llm,
        "trace_repository_failure_count": repository,
        "unknown_failure_count": unknown,
        "top_n": top_n,
    }


def build_trace_latency_metrics(
    traces: list[Trace],
    *,
    slow_threshold_ms: int = 5000,
) -> dict[str, Any]:
    threshold = max(0, int(slow_threshold_ms or 5000))
    durations: list[float] = []
    for trace in traces:
        dur = trace.total_duration_ms
        if dur is None:
            root = getattr(trace, "root_span", None)
            dur = getattr(root, "duration_ms", None) if root is not None else None
        if dur is not None and dur >= 0:
            durations.append(float(dur))
    durations.sort()
    n = len(durations)
    result: dict[str, Any] = {
        "count": len(traces),
        "duration_available_count": n,
        "slow_threshold_ms": threshold,
    }
    if n > 0:
        result["p50_ms"] = _percentile(durations, 50, n)
        result["p90_ms"] = _percentile(durations, 90, n)
        result["p95_ms"] = _percentile(durations, 95, n)
        result["p99_ms"] = _percentile(durations, 99, n)
        result["max_ms"] = round(durations[-1], 4)
        slow = sum(1 for d in durations if d > threshold)
        result["slow_trace_count"] = slow
        result["slow_trace_rate"] = round(slow / n, 4)
    else:
        for p in ("p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"):
            result[p] = None
        result["slow_trace_count"] = 0
        result["slow_trace_rate"] = 0.0
    return result


def build_trace_repository_metrics(
    traces: list[Trace],
    *,
    repository_health: dict[str, Any],
) -> dict[str, Any]:
    health = {
        k: (bool(repository_health.get(k)) if k == "database_url_configured" else repository_health.get(k))
        for k in ALLOWED_HEALTH_KEYS
        if k in repository_health
    }
    if "storage_path" not in health and "storage_path" in repository_health:
        health["storage_path_configured"] = bool(repository_health.get("storage_path"))
    elif "storage_path" not in health:
        health["storage_path_configured"] = False

    repo_failure = 0
    span_counts: dict[str, int] = {}
    for trace in traces:
        analytics = build_trace_analytics(trace)
        if analytics.get("failure_type") == FailureType.TRACE_REPOSITORY_FAILURE.value:
            repo_failure += 1
        root_cause = analytics.get("root_cause_span")
        if isinstance(root_cause, dict):
            name = str(root_cause.get("name") or "")
            if name.startswith("trace.repository"):
                span_counts[name] = span_counts.get(name, 0) + 1

    sorted_spans = sorted(span_counts.items(), key=lambda x: (-x[1], x[0]))
    return {
        "health": health,
        "repository_failure_count": repo_failure,
        "repository_failure_spans": [{"span": k, "count": v} for k, v in sorted_spans],
    }


def build_trace_eval_readiness_metrics(
    traces: list[Trace],
    *,
    eval_dataset_path: Path | None = None,
) -> dict[str, Any]:
    dataset_path = Path(eval_dataset_path) if eval_dataset_path else default_trace_eval_dataset_path()
    eligible_ids: set[str] = set()
    failure_type_eligible: dict[str, int] = {}

    for trace in traces:
        analytics = build_trace_analytics(trace)
        if should_export_trace_to_eval(trace, analytics):
            eligible_ids.add(trace.trace_id)
            ft = str(analytics.get("failure_type") or FailureType.UNKNOWN_FAILURE.value)
            failure_type_eligible[ft] = failure_type_eligible.get(ft, 0) + 1

    dataset_ids: set[str] = set()
    exported_cases = 0
    if dataset_path.exists():
        try:
            with dataset_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        tid = payload.get("trace_id")
                        if tid:
                            dataset_ids.add(str(tid))
                        if payload.get("case_id"):
                            exported_cases += 1
        except Exception:
            pass

    covered = eligible_ids & dataset_ids
    coverage = round(len(covered) / len(eligible_ids), 4) if eligible_ids else 0.0
    unexported = len(eligible_ids - dataset_ids)

    sorted_types = sorted(failure_type_eligible.items(), key=lambda x: (-x[1], x[0]))
    return {
        "eligible_eval_cases": len(eligible_ids),
        "exported_eval_cases": exported_cases,
        "deduplicated_trace_ids": len(dataset_ids),
        "unexported_eligible_eval_cases": unexported,
        "export_coverage_rate": coverage,
        "failure_type_eligible_counts": [{"failure_type": k, "count": v} for k, v in sorted_types],
        "dataset_path_configured": dataset_path.exists(),
    }


def build_trace_operational_metrics(
    traces: list[Trace],
    *,
    repository_health: dict[str, Any] | None = None,
    eval_dataset_path: Path | None = None,
    window_hours: int = 24,
    top_n: int = 10,
    slow_threshold_ms: int = 5000,
) -> dict[str, Any]:
    health = repository_health or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "overview": build_trace_metrics_overview(traces, window_hours=window_hours),
        "failures": build_trace_failure_metrics(traces, top_n=top_n),
        "latency": build_trace_latency_metrics(traces, slow_threshold_ms=slow_threshold_ms),
        "repository": build_trace_repository_metrics(traces, repository_health=health),
        "eval_readiness": build_trace_eval_readiness_metrics(traces, eval_dataset_path=eval_dataset_path),
        "metadata": {
            "window_hours": window_hours,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _norm_status(status: Any) -> str:
    if hasattr(status, "value"):
        return str(status.value)
    return str(status or "")


def _percentile(sorted_vals: list[float], p: float, n: int) -> float:
    return round(sorted_vals[max(0, ceil(p / 100 * n) - 1)], 4)
