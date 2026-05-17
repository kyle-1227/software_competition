from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.schemas.trace import Trace
from app.services.tracing.persistence import sanitize_trace_for_persistence

REASON_SUCCESS_OLD = "success_older_than_keep_days"
REASON_ERROR_OLD = "error_older_than_keep_error_days"
REASON_DEGRADED_OLD = "degraded_older_than_keep_degraded_days"
REASON_EVAL_EXPORTED_OLD = "eval_exported_older_than_keep_eval_exported_days"


@dataclass
class TraceRetentionPolicy:
    keep_days: int = 30
    keep_error_days: int = 90
    keep_degraded_days: int = 90
    keep_eval_exported_days: int = 180
    max_delete: int = 1000
    batch_size: int = 500
    archive_before_delete: bool = True

    def __post_init__(self) -> None:
        for name in (
            "keep_days",
            "keep_error_days",
            "keep_degraded_days",
            "keep_eval_exported_days",
            "max_delete",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if self.batch_size <= 0:
            raise ValueError(f"batch_size must be > 0, got {self.batch_size}")


@dataclass
class TraceCleanupCandidate:
    trace_id: str
    status: str
    closed_at: str
    degraded: bool = False
    fallback_used: bool = False
    eval_exported: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TraceCleanupStats:
    candidates: int = 0
    would_archive: int = 0
    archived: int = 0
    would_delete: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True
    archive_path: str | None = None
    backup_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TraceExportStats:
    candidates: int = 0
    would_export: int = 0
    exported: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_cleanup_candidates(
    traces: Iterable[Trace],
    policy: TraceRetentionPolicy,
    *,
    eval_exported_trace_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[TraceCleanupCandidate]:
    now = now or datetime.now(timezone.utc)
    eval_exported_trace_ids = eval_exported_trace_ids or set()
    candidates: list[TraceCleanupCandidate] = []
    for trace in traces:
        candidate = cleanup_candidate_for_trace(
            trace,
            policy,
            eval_exported=trace.trace_id in eval_exported_trace_ids,
            now=now,
        )
        if candidate is not None:
            candidates.append(candidate)
        if len(candidates) >= max(0, policy.max_delete):
            break
    return candidates


def cleanup_candidate_for_trace(
    trace: Trace,
    policy: TraceRetentionPolicy,
    *,
    eval_exported: bool = False,
    now: datetime | None = None,
) -> TraceCleanupCandidate | None:
    now = now or datetime.now(timezone.utc)
    status = _enum_value(getattr(trace, "status", None))
    if status == "running" or trace.closed_at is None:
        return None
    closed_at = _aware(trace.closed_at)
    degraded = _trace_flag(trace, "degraded")
    fallback_used = _trace_flag(trace, "fallback_used")
    reason: str | None = None
    if eval_exported:
        if _older_than(closed_at, now, policy.keep_eval_exported_days):
            reason = REASON_EVAL_EXPORTED_OLD
    elif degraded or fallback_used:
        if _older_than(closed_at, now, policy.keep_degraded_days):
            reason = REASON_DEGRADED_OLD
    elif status == "error":
        if _older_than(closed_at, now, policy.keep_error_days):
            reason = REASON_ERROR_OLD
    elif status == "success" and _older_than(closed_at, now, policy.keep_days):
        reason = REASON_SUCCESS_OLD
    if reason is None:
        return None
    return TraceCleanupCandidate(
        trace_id=trace.trace_id,
        status=status,
        closed_at=closed_at.isoformat(),
        degraded=degraded,
        fallback_used=fallback_used,
        eval_exported=eval_exported,
        reason=reason,
    )


def load_eval_exported_trace_ids(dataset: Path | None) -> set[str]:
    if dataset is None or not Path(dataset).exists():
        return set()
    trace_ids: set[str] = set()
    with Path(dataset).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("trace_id"):
                trace_ids.add(str(payload["trace_id"]))
    return trace_ids


def write_sanitized_traces_jsonl(traces: Iterable[Trace], output: Path, *, apply: bool) -> TraceExportStats:
    stats = TraceExportStats(dry_run=not apply)
    traces = list(traces)
    stats.candidates = len(traces)
    stats.would_export = len(traces)
    if not apply:
        return stats
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for trace in traces:
            try:
                handle.write(json.dumps(sanitize_trace_for_persistence(trace), ensure_ascii=False, default=str) + "\n")
                stats.exported += 1
            except Exception:
                stats.failed += 1
    return stats


def _older_than(closed_at: datetime, now: datetime, days: int) -> bool:
    return closed_at < now - timedelta(days=max(0, int(days)))


def _trace_flag(trace: Trace, key: str) -> bool:
    root = getattr(trace, "root_span", None)
    if root is None:
        return False
    return any(_span_flag(span, key) for span in _walk_spans(root))


def _span_flag(span: Any, key: str) -> bool:
    metadata = getattr(span, "metadata", None)
    outputs = getattr(span, "outputs", None)
    return bool(
        getattr(span, key, False)
        or (metadata.get(key) if isinstance(metadata, dict) else False)
        or (outputs.get(key) if isinstance(outputs, dict) else False)
    )


def _walk_spans(span: Any):
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
