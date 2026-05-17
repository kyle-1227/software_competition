from __future__ import annotations

import argparse
from pathlib import Path

from app.services.tracing.reader import iter_traces_from_jsonl
from app.services.tracing.repository import PostgreSQLTraceRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import JSONL traces into PostgreSQL.")
    parser.add_argument("--trace-file", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args(argv)

    repository = PostgreSQLTraceRepository(args.database_url)
    repository.initialize()
    imported = 0
    for trace in iter_traces_from_jsonl(args.trace_file):
        if args.commit:
            repository.save_trace(trace)
            for span in _walk_spans(trace.root_span):
                if span is trace.root_span:
                    continue
                repository.save_span(trace.trace_id, span)
            repository.close_trace(trace)
        imported += 1
    mode = "imported" if args.commit else "validated"
    print(f"{mode} {imported} traces")
    return 0


def _walk_spans(span):
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


if __name__ == "__main__":
    raise SystemExit(main())
