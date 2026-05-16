from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.services.tracing.reader import load_trace_from_jsonl, sanitize_trace_for_export
from app.services.tracing.summary import build_trace_summary
from app.services.tracing.timeline import build_trace_timeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a persisted trace by trace_id.")
    parser.add_argument("--trace-id", required=True)
    parser.add_argument(
        "--format",
        choices=["summary", "timeline", "raw"],
        default="summary",
    )
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    trace_file = args.trace_file or _default_trace_file()
    trace = load_trace_from_jsonl(trace_file, args.trace_id)
    if trace is None:
        print(f"Trace not found: {args.trace_id}", file=sys.stderr)
        return 1

    if args.format == "timeline":
        rendered = build_trace_timeline(trace)
    elif args.format == "raw":
        rendered = _json_dump(sanitize_trace_for_export(trace), pretty=args.pretty)
    else:
        rendered = _json_dump(build_trace_summary(trace), pretty=args.pretty)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


def _default_trace_file() -> Path:
    from app.services.tracing.reader import _default_trace_file as default_trace_file

    return default_trace_file(None)


def _json_dump(data: dict[str, Any], *, pretty: bool) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2 if pretty else None)


if __name__ == "__main__":
    raise SystemExit(main())
