from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSONL_PATH = BACKEND_ROOT.parent / "data" / "traces" / "traces.jsonl"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.tracing.retention import (  # noqa: E402
    TraceCleanupStats,
    TraceRetentionPolicy,
    cleanup_jsonl_traces,
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

    try:
        policy = TraceRetentionPolicy(
            keep_days=args.keep_days,
            keep_error_days=args.keep_error_days,
            keep_degraded_days=args.keep_degraded_days,
            keep_eval_exported_days=args.keep_eval_exported_days,
            max_delete=args.max_delete,
            batch_size=args.batch_size,
            archive_before_delete=args.archive_before_delete,
        )
    except ValueError as exc:
        _warn(sys.stderr, "trace_cleanup_invalid_policy", exc)
        print(json.dumps(TraceCleanupStats(fatal=True).to_dict(), ensure_ascii=False))
        return 1

    if args.backend == "postgres":
        _warn(sys.stderr, "trace_cleanup_postgres_not_implemented", NotImplementedError("PostgreSQL cleanup is not implemented yet"))
        print(json.dumps(TraceCleanupStats(fatal=True).to_dict(), ensure_ascii=False))
        return 1

    jsonl_path = args.jsonl_path or DEFAULT_JSONL_PATH
    stats = cleanup_jsonl_traces(
        jsonl_path,
        policy=policy,
        eval_dataset_path=args.eval_dataset,
        archive_path=args.archive_path,
        apply=args.apply,
    )
    print(json.dumps(stats.to_dict(), ensure_ascii=False))
    return 1 if stats.fatal else 0


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
