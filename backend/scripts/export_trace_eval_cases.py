from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.schemas.trace import Trace  # noqa: E402
from app.services.tracing.analytics import build_trace_analytics  # noqa: E402
from app.services.tracing.eval_adapter import (  # noqa: E402
    should_export_trace_to_eval,
    trace_to_eval_case,
)
from app.services.tracing.eval_dataset import (  # noqa: E402
    TraceEvalDatasetWriter,
    default_trace_eval_dataset_path,
)
from app.services.tracing.repository import (  # noqa: E402
    JsonlTraceRepository,
    PostgreSQLTraceRepository,
    build_trace_repository,
)
from app.services.tracing.serializers import redact_trace_text  # noqa: E402


@dataclass
class EvalExportOptions:
    dataset: Path
    trace_id: str | None = None
    session_id: str | None = None
    status: str | None = None
    failure_type: str | None = None
    limit: int = 100
    include_success: bool = False
    database_url: str | None = None
    trace_backend: str = "auto"
    apply: bool = False
    verbose: bool = False


@dataclass
class EvalExportStats:
    traces_seen: int = 0
    eligible: int = 0
    exported: int = 0
    deduplicated: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RepositoryFactory = Callable[[EvalExportOptions], Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export trace-derived regression eval cases.")
    parser.add_argument("--dataset", type=Path, default=default_trace_eval_dataset_path())
    parser.add_argument("--trace-id")
    parser.add_argument("--session-id")
    parser.add_argument("--status", choices=["success", "error", "running", "cancelled"])
    parser.add_argument("--failure-type")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--include-success", action="store_true")
    parser.add_argument(
        "--database-url",
        default=getattr(settings, "trace_database_url", None)
        or getattr(settings, "database_url", None),
    )
    parser.add_argument(
        "--trace-backend",
        choices=["postgres", "jsonl", "auto"],
        default=getattr(settings, "trace_backend", "auto"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    options = EvalExportOptions(
        dataset=args.dataset,
        trace_id=args.trace_id,
        session_id=args.session_id,
        status=args.status,
        failure_type=args.failure_type,
        limit=max(1, int(args.limit or 100)),
        include_success=args.include_success,
        database_url=args.database_url,
        trace_backend=args.trace_backend,
        apply=args.apply,
        verbose=args.verbose,
    )
    stats = export_trace_eval_cases(options, stderr=sys.stderr)
    print(json.dumps(stats.to_dict(), ensure_ascii=False))
    return 1 if stats.failed else 0


def export_trace_eval_cases(
    options: EvalExportOptions,
    *,
    repository: Any | None = None,
    repository_factory: RepositoryFactory | None = None,
    writer: TraceEvalDatasetWriter | None = None,
    stderr: TextIO | None = None,
) -> EvalExportStats:
    stats = EvalExportStats(dry_run=not options.apply)
    try:
        repository = repository or (
            repository_factory(options) if repository_factory is not None else _build_repository(options)
        )
    except Exception as exc:
        stats.failed += 1
        _warn(stderr, "repository_initialize_failed", error=exc)
        return stats

    try:
        traces = _candidate_traces(repository, options)
    except Exception as exc:
        stats.failed += 1
        _warn(stderr, "trace_listing_failed", error=exc)
        return stats

    writer = writer or TraceEvalDatasetWriter(options.dataset)
    for trace in traces:
        if trace is None:
            stats.skipped += 1
            continue
        stats.traces_seen += 1
        try:
            full_trace = _load_full_trace(repository, trace)
            if full_trace is None:
                stats.skipped += 1
                continue
            analytics = build_trace_analytics(full_trace)
            failure_type = str(analytics.get("failure_type") or "")
            if options.failure_type and failure_type != options.failure_type:
                stats.skipped += 1
                continue
            eligible = should_export_trace_to_eval(
                full_trace,
                analytics,
                include_success=options.include_success,
            )
            if not eligible:
                stats.skipped += 1
                continue
            stats.eligible += 1
            case = trace_to_eval_case(
                full_trace,
                source="cli_export",
                analytics=analytics,
                include_success=options.include_success,
            )
            if not options.apply:
                continue
            if writer.append_case(case):
                stats.exported += 1
            else:
                stats.deduplicated += 1
        except Exception as exc:
            stats.failed += 1
            _warn(
                stderr,
                "trace_eval_case_export_failed",
                trace_id=getattr(trace, "trace_id", None),
                error=exc,
                verbose=options.verbose,
            )
            continue
    return stats


def _candidate_traces(repository: Any, options: EvalExportOptions) -> list[Trace | None]:
    if options.trace_id:
        return [repository.get_trace(options.trace_id)]
    traces = repository.list_traces(
        limit=options.limit,
        session_id=options.session_id,
        status=options.status,
    )
    return list(traces or [])[: options.limit]


def _load_full_trace(repository: Any, trace: Trace) -> Trace | None:
    trace_id = getattr(trace, "trace_id", None)
    if not trace_id:
        return None
    loaded = repository.get_trace(trace_id)
    return loaded or trace


def _build_repository(options: EvalExportOptions) -> Any:
    if options.trace_backend == "postgres" or (
        options.trace_backend == "auto" and options.database_url
    ):
        if not options.database_url:
            raise RuntimeError("TRACE_DATABASE_URL or DATABASE_URL is required for postgres trace export")
        repository = PostgreSQLTraceRepository(str(options.database_url), configured_backend=options.trace_backend)
        repository.initialize()
        return repository
    if options.trace_backend == "jsonl":
        repository = JsonlTraceRepository(configured_backend="jsonl")
        repository.initialize()
        return repository
    return build_trace_repository()


def _warn(
    stderr: TextIO | None,
    event: str,
    *,
    trace_id: str | None = None,
    error: Exception | None = None,
    verbose: bool = False,
) -> None:
    if stderr is None:
        return
    payload: dict[str, Any] = {"event": event}
    if trace_id:
        payload["trace_id"] = str(trace_id)
    if error is not None:
        payload["error_type"] = error.__class__.__name__
        payload["error_summary"] = redact_trace_text(str(error))[:500]
    if verbose:
        payload["verbose"] = True
    stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
