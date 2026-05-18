from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.tracing.eval_adapter import (  # noqa: E402
    EvalExportOptions,
    export_trace_eval_cases,
)
from app.services.tracing.eval_dataset import default_trace_eval_dataset_path  # noqa: E402


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


if __name__ == "__main__":
    raise SystemExit(main())
