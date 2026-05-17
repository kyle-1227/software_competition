from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.schemas.trace import Trace  # noqa: E402
from app.services.tracing.persistence import sanitize_trace_for_persistence  # noqa: E402
from app.services.tracing.repository import JsonlTraceRepository, PostgreSQLTraceRepository, build_trace_repository  # noqa: E402
from app.services.tracing.retention import (  # noqa: E402
    TraceCleanupStats,
    TraceRetentionPolicy,
    load_eval_exported_trace_ids,
    select_cleanup_candidates,
)
from app.services.tracing.serializers import redact_trace_text  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cleanup old trace data using a retention policy.")
    parser.add_argument("--backend", choices=["postgres", "jsonl", "auto"], default=getattr(settings, "trace_backend", "auto"))
    parser.add_argument("--database-url", default=getattr(settings, "trace_database_url", None) or getattr(settings, "database_url", None))
    parser.add_argument("--jsonl-path", type=Path)
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--keep-error-days", type=int, default=90)
    parser.add_argument("--keep-degraded-days", type=int, default=90)
    parser.add_argument("--keep-eval-exported-days", type=int, default=180)
    parser.add_argument("--max-delete", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--archive-before-delete", action="store_true", default=True)
    parser.add_argument("--no-archive-before-delete", dest="archive_before_delete", action="store_false")
    parser.add_argument("--archive-path", type=Path)
    parser.add_argument("--eval-dataset", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    policy = TraceRetentionPolicy(
        keep_days=args.keep_days,
        keep_error_days=args.keep_error_days,
        keep_degraded_days=args.keep_degraded_days,
        keep_eval_exported_days=args.keep_eval_exported_days,
        max_delete=args.max_delete,
        batch_size=args.batch_size,
        archive_before_delete=args.archive_before_delete,
    )
    repository = _build_repository(args.backend, database_url=args.database_url, jsonl_path=args.jsonl_path)
    stats = cleanup_traces(
        repository,
        backend=args.backend,
        policy=policy,
        apply=args.apply,
        archive_path=args.archive_path,
        eval_dataset=args.eval_dataset,
        stderr=sys.stderr,
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False))
    return 1 if stats.failed else 0


def cleanup_traces(
    repository: Any,
    *,
    backend: str,
    policy: TraceRetentionPolicy,
    apply: bool = False,
    archive_path: Path | None = None,
    eval_dataset: Path | None = None,
    stderr: TextIO | None = None,
) -> TraceCleanupStats:
    if backend == "jsonl" and _is_eval_path(getattr(repository, "storage_path", None)):
        stats = TraceCleanupStats(dry_run=not apply, failed=1)
        _warn(stderr, "refusing_to_clean_eval_path", RuntimeError("eval paths are not trace cleanup targets"))
        return stats
    eval_exported = load_eval_exported_trace_ids(eval_dataset)
    if backend == "jsonl":
        return cleanup_jsonl(repository, policy=policy, apply=apply, archive_path=archive_path, eval_exported=eval_exported, stderr=stderr)
    return cleanup_repository(repository, policy=policy, apply=apply, archive_path=archive_path, eval_exported=eval_exported, stderr=stderr)


def cleanup_repository(
    repository: Any,
    *,
    policy: TraceRetentionPolicy,
    apply: bool,
    archive_path: Path | None,
    eval_exported: set[str],
    stderr: TextIO | None = None,
) -> TraceCleanupStats:
    stats = TraceCleanupStats(dry_run=not apply)
    try:
        traces = repository.list_traces(limit=policy.max_delete)
        candidates = select_cleanup_candidates(traces, policy, eval_exported_trace_ids=eval_exported)
        stats.candidates = len(candidates)
        stats.would_delete = len(candidates)
        stats.would_archive = len(candidates) if policy.archive_before_delete else 0
        if not apply:
            return stats
        candidate_ids = {candidate.trace_id for candidate in candidates}
        candidate_traces = [trace for trace in traces if trace.trace_id in candidate_ids]
        if policy.archive_before_delete and candidate_traces:
            _write_archive(candidate_traces, archive_path or _default_archive_path())
            stats.archived = len(candidate_traces)
        if candidate_ids:
            stats.deleted = repository.delete_traces(list(candidate_ids), batch_size=policy.batch_size)
        return stats
    except Exception as exc:
        stats.failed += 1
        _warn(stderr, "trace_cleanup_failed", exc)
        return stats


def cleanup_jsonl(
    repository: JsonlTraceRepository,
    *,
    policy: TraceRetentionPolicy,
    apply: bool,
    archive_path: Path | None,
    eval_exported: set[str],
    stderr: TextIO | None = None,
) -> TraceCleanupStats:
    stats = TraceCleanupStats(dry_run=not apply)
    trace_file = repository.storage_path / "traces.jsonl"
    if not trace_file.exists():
        return stats
    kept_lines: list[str] = []
    deleted_ids: set[str] = set()
    candidate_traces: list[Trace] = []
    try:
        for line in trace_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                kept_lines.append(line)
                stats.skipped += 1
                continue
            try:
                trace = Trace.model_validate(json.loads(line))
            except Exception:
                kept_lines.append(line)
                stats.skipped += 1
                continue
            candidate = select_cleanup_candidates([trace], policy, eval_exported_trace_ids=eval_exported)
            if candidate:
                stats.candidates += 1
                stats.would_delete += 1
                if policy.archive_before_delete:
                    stats.would_archive += 1
                candidate_traces.append(trace)
                deleted_ids.add(trace.trace_id)
                if stats.candidates > max(0, policy.max_delete):
                    kept_lines.append(line)
                    deleted_ids.discard(trace.trace_id)
                    candidate_traces.pop()
                    stats.candidates -= 1
                    stats.would_delete -= 1
                    if policy.archive_before_delete:
                        stats.would_archive -= 1
                    stats.skipped += 1
                continue
            kept_lines.append(line)
        if not apply:
            return stats
        if policy.archive_before_delete and candidate_traces:
            _write_archive(candidate_traces, archive_path or _default_archive_path())
            stats.archived = len(candidate_traces)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup = trace_file.with_suffix(f".jsonl.bak.{timestamp}")
        tmp = trace_file.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""), encoding="utf-8")
        trace_file.replace(backup)
        tmp.replace(trace_file)
        stats.deleted = len(deleted_ids)
        return stats
    except Exception as exc:
        stats.failed += 1
        _warn(stderr, "jsonl_trace_cleanup_failed", exc)
        return stats


def _build_repository(backend: str, *, database_url: str | None, jsonl_path: Path | None):
    if backend == "postgres":
        repository = PostgreSQLTraceRepository(str(database_url or ""), configured_backend="postgres")
        repository.initialize()
        return repository
    if backend == "jsonl":
        repository = JsonlTraceRepository(jsonl_path, configured_backend="jsonl")
        repository.initialize()
        return repository
    return build_trace_repository(jsonl_path) if jsonl_path else build_trace_repository()


def _write_archive(traces: list[Trace], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as handle:
        for trace in traces:
            handle.write(json.dumps(sanitize_trace_for_persistence(trace), ensure_ascii=False, default=str) + "\n")


def _default_archive_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "exports" / f"trace_archive_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.jsonl"


def _is_eval_path(path: Any) -> bool:
    if path is None:
        return False
    text = str(path).replace("\\", "/")
    return "/evals/datasets" in text or "/evals/reports" in text


def _warn(stderr: TextIO | None, event: str, exc: Exception) -> None:
    if stderr is None:
        return
    stderr.write(
        json.dumps(
            {
                "event": event,
                "error_type": exc.__class__.__name__,
                "error_summary": redact_trace_text(str(exc))[:500],
            },
            ensure_ascii=False,
        )
        + "\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
