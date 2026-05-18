from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_JSONL_PATH = PROJECT_ROOT / "data" / "traces" / "traces.jsonl"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.schemas.trace import Trace  # noqa: E402
from app.services.tracing.persistence import sanitize_trace_for_persistence  # noqa: E402
from app.services.tracing.repository import (  # noqa: E402
    JsonlTraceRepository,
    PostgreSQLTraceRepository,
    build_trace_repository,
)
from app.services.tracing.retention import TraceExportStats  # noqa: E402
from app.services.tracing.serializers import redact_trace_text  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export sanitized traces to JSONL.")
    parser.add_argument("--backend", choices=["postgres", "jsonl", "auto"], default=getattr(settings, "trace_backend", "auto"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--status", choices=["success", "error", "running", "cancelled"])
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--limit", type=int, default=1000)
    parser.set_defaults(include_spans=True)
    parser.add_argument("--no-include-spans", action="store_false", dest="include_spans")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--database-url", default=getattr(settings, "trace_database_url", None) or getattr(settings, "database_url", None))
    parser.add_argument("--jsonl-path", type=Path)
    args = parser.parse_args(argv)

    if is_forbidden_export_path(args.output):
        _warn(sys.stderr, "trace_export_forbidden_output", ValueError(str(args.output)))
        print(json.dumps(TraceExportStats(fatal=True, output_path=str(args.output)).to_dict(), ensure_ascii=False))
        return 1

    if args.backend == "postgres" and not args.database_url:
        _warn(sys.stderr, "trace_export_missing_db_url", ValueError("database_url required for postgres backend"))
        print(json.dumps(TraceExportStats(fatal=True, output_path=str(args.output)).to_dict(), ensure_ascii=False))
        return 1

    if args.backend == "jsonl":
        repository = None
        jsonl_path = args.jsonl_path or DEFAULT_JSONL_PATH
    else:
        repository = _build_repository(args.backend, database_url=args.database_url, jsonl_path=args.jsonl_path)
        jsonl_path = args.jsonl_path
    stats = export_traces(
        repository,
        output=args.output,
        backend=args.backend,
        jsonl_path=jsonl_path,
        session_id=args.session_id,
        status=args.status,
        before=_parse_datetime(args.before),
        after=_parse_datetime(args.after),
        limit=args.limit,
        include_spans=args.include_spans,
        apply=args.apply,
        stderr=sys.stderr,
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False))
    return 1 if stats.fatal else 0


def export_traces(
    repository: Any,
    *,
    output: Path,
    backend: str | None = None,
    jsonl_path: Path | None = None,
    session_id: str | None = None,
    status: str | None = None,
    before: datetime | None = None,
    after: datetime | None = None,
    limit: int | None = None,
    include_spans: bool = True,
    apply: bool = False,
    stderr: TextIO | None = None,
) -> TraceExportStats:
    stats = TraceExportStats(dry_run=not apply, output_path=str(output))

    if backend == "jsonl" and jsonl_path:
        traces, skipped_count, failed_count = _load_jsonl_with_stats(jsonl_path, stderr)
        stats.skipped += skipped_count
        stats.failed += failed_count
    else:
        try:
            traces = repository.list_traces(limit=limit, session_id=session_id, status=status)
        except Exception as exc:
            stats.failed += 1
            stats.fatal = True
            _warn(stderr, "trace_export_list_failed", exc)
            return stats

    selected: list[Trace] = []
    for trace in traces:
        if not _within_window(trace, before=before, after=after):
            stats.skipped += 1
            continue
        if limit is not None and len(selected) >= max(0, int(limit)):
            break
        selected.append(_without_spans(trace) if not include_spans else trace)
    stats.candidates = len(selected)
    stats.would_export = len(selected)
    if not apply:
        return stats
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            for trace in selected:
                handle.write(json.dumps(sanitize_trace_for_persistence(trace), ensure_ascii=False, default=str) + "\n")
                stats.exported += 1
    except Exception as exc:
        stats.failed += 1
        stats.fatal = True
        _warn(stderr, "trace_export_write_failed", exc)
    return stats


def is_forbidden_export_path(path: Path) -> bool:
    project_root = BACKEND_ROOT.parent
    resolved = Path(path).resolve()
    if resolved == (project_root / "evals" / "datasets" / "trace_regression_cases.jsonl").resolve():
        return True
    reports_dir = (project_root / "evals" / "reports").resolve()
    try:
        resolved.relative_to(reports_dir)
        return resolved.suffix in {".json", ".jsonl", ".md"}
    except ValueError:
        pass
    return False


def _load_jsonl_with_stats(jsonl_path: Path, stderr: TextIO | None) -> tuple[list[Trace], int, int]:
    if not jsonl_path.exists():
        return [], 0, 0
    traces: list[Trace] = []
    skipped = 0
    failed = 0
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                skipped += 1
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                failed += 1
                _warn(stderr, "trace_export_bad_line", ValueError("bad JSON line"))
                continue
            try:
                traces.append(Trace.model_validate(payload))
            except Exception as exc:
                failed += 1
                _warn(stderr, "trace_export_bad_line", exc)
    return traces, skipped, failed


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


def _within_window(trace: Trace, *, before: datetime | None, after: datetime | None) -> bool:
    value = trace.closed_at or trace.created_at
    value = _aware(value)
    if before is not None and value >= before:
        return False
    if after is not None and value < after:
        return False
    return True


def _without_spans(trace: Trace) -> Trace:
    copy = trace.model_copy(deep=True)
    copy.root_span.children = []
    return copy


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
