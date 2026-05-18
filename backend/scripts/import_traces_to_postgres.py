from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.tracing.trace_import import (  # noqa: E402
    ImportOptions,
    import_traces,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import JSONL traces into PostgreSQL.")
    parser.add_argument("--file", "--trace-file", dest="file", type=Path, default=_default_trace_file())
    parser.add_argument(
        "--database-url",
        default=getattr(settings, "trace_database_url", None)
        or getattr(settings, "database_url", None),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--trace-id")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    options = ImportOptions(
        file=args.file,
        database_url=args.database_url,
        apply=args.apply,
        limit=args.limit,
        trace_id=args.trace_id,
        fail_fast=args.fail_fast,
        skip_existing=args.skip_existing,
        verbose=args.verbose,
    )
    stats = import_traces(options, stderr=sys.stderr)
    print(json.dumps(stats.to_dict(), ensure_ascii=False))
    return 1 if stats.failed else 0


def _default_trace_file() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "traces" / "traces.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
