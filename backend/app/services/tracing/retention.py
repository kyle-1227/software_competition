from __future__ import annotations

import json
import shutil
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
    fatal: bool = False

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
    output_path: str | None = None
    fatal: bool = False

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


def cleanup_candidate_for_row(
    row: dict[str, Any],
    policy: TraceRetentionPolicy,
    *,
    eval_exported: bool = False,
    now: datetime | None = None,
) -> TraceCleanupCandidate | None:
    now = now or datetime.now(timezone.utc)
    status = str(row.get("status") or "")
    closed_at_value = row.get("closed_at")
    if status == "running" or closed_at_value is None:
        return None
    closed_at = _aware_from_value(closed_at_value)
    degraded = bool(row.get("degraded"))
    fallback_used = bool(row.get("fallback_used"))
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
        trace_id=str(row["trace_id"]),
        status=status,
        closed_at=closed_at.isoformat(),
        degraded=degraded,
        fallback_used=fallback_used,
        eval_exported=eval_exported,
        reason=reason,
    )


def select_cleanup_candidates_from_rows(
    rows: Iterable[dict[str, Any]],
    policy: TraceRetentionPolicy,
    *,
    eval_exported_trace_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[TraceCleanupCandidate]:
    now = now or datetime.now(timezone.utc)
    eval_exported_trace_ids = eval_exported_trace_ids or set()
    candidates: list[TraceCleanupCandidate] = []
    for row in rows:
        candidate = cleanup_candidate_for_row(
            row,
            policy,
            eval_exported=str(row.get("trace_id") or "") in eval_exported_trace_ids,
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


def cleanup_jsonl_traces(
    jsonl_path: Path,
    *,
    policy: TraceRetentionPolicy,
    eval_dataset_path: Path | None = None,
    archive_path: Path | None = None,
    apply: bool = False,
) -> TraceCleanupStats:
    stats = TraceCleanupStats(dry_run=not apply)
    eval_exported_trace_ids = load_eval_exported_trace_ids(eval_dataset_path)

    if not jsonl_path.exists():
        return stats

    kept_lines: list[str] = []
    candidate_traces: list[Trace] = []
    deleted_count = 0
    try:
        raw = jsonl_path.read_text(encoding="utf-8")
    except Exception as exc:
        stats.failed += 1
        stats.fatal = True
        return stats
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append(line)
            stats.skipped += 1
            continue
        try:
            payload = json.loads(line)
            trace = Trace.model_validate(payload)
        except Exception:
            kept_lines.append(line)
            stats.failed += 1
            continue
        candidate = cleanup_candidate_for_trace(
            trace, policy, eval_exported=trace.trace_id in eval_exported_trace_ids
        )
        if candidate is not None and deleted_count < max(0, policy.max_delete):
            stats.candidates += 1
            stats.would_delete += 1
            if policy.archive_before_delete:
                stats.would_archive += 1
            candidate_traces.append(trace)
            deleted_count += 1
        else:
            kept_lines.append(line)

    if not apply:
        if stats.candidates > 0 and policy.archive_before_delete:
            stats.archive_path = str(archive_path or _default_archive_path())
        return stats

    if deleted_count == 0:
        return stats

    if policy.archive_before_delete and candidate_traces:
        archive = archive_path or _default_archive_path()
        try:
            _write_archive(candidate_traces, archive)
            stats.archived = len(candidate_traces)
        except Exception:
            stats.failed += 1
            stats.fatal = True
            return stats
        stats.archive_path = str(archive)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = jsonl_path.with_name(f"{jsonl_path.name}.bak.{timestamp}")
    tmp = jsonl_path.with_name(f"{jsonl_path.name}.tmp")
    try:
        tmp.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
        shutil.copy2(jsonl_path, backup)
        tmp.replace(jsonl_path)
        stats.deleted = deleted_count
        stats.backup_path = str(backup)
    except Exception:
        stats.failed += 1
        stats.fatal = True
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return stats
    return stats


def cleanup_postgres_traces(
    repository: Any,
    *,
    policy: TraceRetentionPolicy,
    eval_dataset_path: Path | None = None,
    archive_path: Path | None = None,
    apply: bool = False,
    scan_limit: int | None = None,
) -> TraceCleanupStats:
    stats = TraceCleanupStats(dry_run=not apply)
    eval_exported_trace_ids = load_eval_exported_trace_ids(eval_dataset_path)
    limit = scan_limit or max(policy.max_delete * 5, 1000)

    try:
        rows = repository.list_trace_cleanup_rows(limit=limit)
    except Exception:
        stats.failed += 1
        stats.fatal = True
        return stats

    candidates = select_cleanup_candidates_from_rows(
        rows, policy, eval_exported_trace_ids=eval_exported_trace_ids
    )
    stats.candidates = len(candidates)
    stats.would_delete = len(candidates)
    if policy.archive_before_delete:
        stats.would_archive = len(candidates)

    if not apply:
        if stats.candidates > 0 and policy.archive_before_delete:
            stats.archive_path = str(archive_path or _default_archive_path())
        return stats

    if not candidates:
        return stats

    candidate_ids = [c.trace_id for c in candidates]

    if policy.archive_before_delete:
        archive_traces: list[Trace] = []
        for trace_id in candidate_ids:
            try:
                trace = repository.get_trace(trace_id)
            except Exception:
                stats.failed += 1
                stats.fatal = True
                return stats
            if trace is None:
                stats.failed += 1
                stats.fatal = True
                return stats
            archive_traces.append(trace)
        archive = archive_path or _default_archive_path()
        try:
            _write_archive(archive_traces, archive)
            stats.archived = len(archive_traces)
        except Exception:
            stats.failed += 1
            stats.fatal = True
            return stats
        stats.archive_path = str(archive)

    try:
        stats.deleted = repository.delete_traces(candidate_ids, batch_size=policy.batch_size)
    except Exception:
        stats.failed += 1
        stats.fatal = True
    return stats


def _write_archive(traces: list[Trace], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("w", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(json.dumps(sanitize_trace_for_persistence(trace), ensure_ascii=False, default=str) + "\n")


def _default_archive_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "exports" / f"trace_archive_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.jsonl"


def _aware_from_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return _aware(parsed)
    raise ValueError(f"cannot parse datetime from {type(value).__name__}")


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
