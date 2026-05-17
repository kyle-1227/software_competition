from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import settings
from app.services.tracing.reader import iter_traces_from_jsonl
from app.services.tracing.repository import PostgreSQLTraceRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import JSONL traces into PostgreSQL.")
    parser.add_argument("--trace-file", type=Path, default=_default_trace_file())
    parser.add_argument(
        "--database-url",
        default=getattr(settings, "trace_database_url", None)
        or getattr(settings, "database_url", None),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if not args.database_url:
        print("PostgreSQL database URL is required.")
        return 1

    repository = PostgreSQLTraceRepository(args.database_url)
    repository.initialize()
    scanned = 0
    imported = 0
    failed = 0
    for trace in iter_traces_from_jsonl(args.trace_file):
        scanned += 1
        try:
            if args.apply:
                repository.save_trace(trace)
                for span in _walk_spans(trace.root_span):
                    if span is trace.root_span:
                        continue
                    repository.save_span(trace.trace_id, span)
                repository.close_trace(trace)
                imported += 1
        except Exception:
            failed += 1
    mode = "imported" if args.apply else "validated"
    print(
        f"{mode} traces: scanned={scanned} valid={scanned} "
        f"imported={imported} skipped={scanned - imported if not args.apply else 0} "
        f"failed={failed}"
    )
    return 0


def _walk_spans(span):
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _default_trace_file() -> Path:
    storage = Path(getattr(settings, "trace_storage_path", "../data/traces"))
    if not storage.is_absolute():
        storage = Path(__file__).resolve().parents[2] / storage
    return storage / "traces.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
