from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, TextIO

from pydantic import ValidationError

from app.schemas.trace import Trace, TraceSpan
from app.services.tracing.persistence import question_persistence_fields
from app.services.tracing.repository import PostgreSQLTraceRepository
from app.services.tracing.serializers import redact_trace_text


@dataclass
class ImportOptions:
    file: Path
    database_url: str | None = None
    apply: bool = False
    limit: int | None = None
    trace_id: str | None = None
    fail_fast: bool = False
    skip_existing: bool = False
    verbose: bool = False


@dataclass
class ImportStats:
    lines_seen: int = 0
    valid_traces: int = 0
    would_import: int = 0
    imported: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


RepositoryFactory = Callable[[str], Any]


def import_traces(
    options: ImportOptions,
    *,
    repository_factory: RepositoryFactory = PostgreSQLTraceRepository,
    stderr: TextIO | None = None,
) -> ImportStats:
    stats = ImportStats(dry_run=not options.apply)
    trace_file = Path(options.file)
    if not trace_file.exists():
        stats.failed += 1
        _warn_import(stderr, "trace_file_missing", file=str(trace_file))
        return stats

    repository = None
    if options.apply:
        if not options.database_url:
            stats.failed += 1
            _warn_import(stderr, "database_url_required")
            return stats
        try:
            repository = repository_factory(str(options.database_url))
            repository.initialize()
        except Exception as exc:
            stats.failed += 1
            _warn_import(stderr, "repository_initialize_failed", error=exc)
            return stats

    processed_eligible = 0
    with trace_file.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stats.lines_seen += 1
            if not line.strip():
                stats.skipped += 1
                continue

            raw: dict[str, Any] | None = None
            trace: Trace | None = None
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("trace JSONL row must be an object")
                normalized = normalize_trace_payload_for_import(raw)
                trace = Trace.model_validate(normalized)
                stats.valid_traces += 1
            except Exception as exc:
                stats.failed += 1
                _warn_import(stderr, "trace_parse_failed", line_number=line_number, raw=raw, error=exc, verbose=options.verbose)
                if options.fail_fast:
                    break
                continue

            if options.trace_id and trace.trace_id != options.trace_id:
                stats.skipped += 1
                continue

            if options.limit is not None and processed_eligible >= max(0, options.limit):
                stats.skipped += 1
                continue

            if repository is not None and options.skip_existing:
                try:
                    if repository.get_trace(trace.trace_id) is not None:
                        stats.skipped += 1
                        continue
                except Exception as exc:
                    stats.failed += 1
                    _warn_import(stderr, "trace_existing_check_failed", line_number=line_number, trace=trace, error=exc, verbose=options.verbose)
                    if options.fail_fast:
                        break
                    continue

            processed_eligible += 1
            stats.would_import += 1

            if repository is None:
                continue

            try:
                repository.save_trace(trace)
                for span in iter_import_spans(trace):
                    repository.save_span(trace.trace_id, span)
                repository.close_trace(trace)
                stats.imported += 1
            except Exception as exc:
                stats.failed += 1
                _warn_import(stderr, "trace_import_failed", line_number=line_number, trace=trace, error=exc, verbose=options.verbose)
                if options.fail_fast:
                    break
                continue
    return stats


def normalize_trace_payload_for_import(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    trace_id = str(normalized.get("trace_id") or "")
    root = normalized.get("root_span")
    if isinstance(root, dict):
        root_parent_id = str(root.get("span_id") or f"{trace_id}:root")
        _normalize_child_spans(
            root.get("children") if isinstance(root.get("children"), list) else [],
            trace_id=trace_id,
            parent_span_id=root_parent_id,
            path="root",
        )
    return normalized


def iter_import_spans(trace: Trace):
    for index, child in enumerate(getattr(trace.root_span, "children", []) or []):
        yield from _walk_import_spans(child, f"root.{index}")


def _walk_import_spans(span: TraceSpan, path: str):
    del path
    yield span
    for index, child in enumerate(getattr(span, "children", []) or []):
        yield from _walk_import_spans(child, f"{span.span_id}.{index}")


def _normalize_child_spans(
    children: list[Any],
    *,
    trace_id: str,
    parent_span_id: str,
    path: str,
) -> None:
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            continue
        child_path = f"{path}.{index}"
        if not child.get("span_id"):
            child["span_id"] = _deterministic_span_id(trace_id, child_path, child, index)
        child["trace_id"] = trace_id
        child["parent_span_id"] = parent_span_id
        nested = child.get("children")
        if isinstance(nested, list):
            _normalize_child_spans(
                nested,
                trace_id=trace_id,
                parent_span_id=str(child["span_id"]),
                path=child_path,
            )


def _deterministic_span_id(
    trace_id: str,
    path: str,
    span: dict[str, Any],
    sibling_index: int,
) -> str:
    material = "|".join(
        (
            trace_id,
            path,
            str(span.get("name") or ""),
            str(span.get("start_time") or ""),
            str(sibling_index),
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()[:32]


def _warn_import(
    stderr: TextIO | None,
    event: str,
    *,
    line_number: int | None = None,
    raw: dict[str, Any] | None = None,
    trace: Trace | None = None,
    error: Exception | None = None,
    file: str | None = None,
    verbose: bool = False,
) -> None:
    if stderr is None:
        return
    payload: dict[str, Any] = {"event": event}
    if line_number is not None:
        payload["line_number"] = line_number
    if file is not None:
        payload["file"] = file
    trace_id = getattr(trace, "trace_id", None) or (raw.get("trace_id") if isinstance(raw, dict) else None)
    if trace_id:
        payload["trace_id"] = str(trace_id)
    question = getattr(trace, "question", None)
    if question is None and isinstance(raw, dict):
        question = raw.get("question")
    question_fields = question_persistence_fields(str(question or ""), "summary")
    if question_fields.get("question_hash"):
        payload["question_hash"] = question_fields["question_hash"]
    if verbose and question_fields.get("question_preview"):
        payload["question_preview"] = question_fields["question_preview"]
    if error is not None:
        payload["error_type"] = error.__class__.__name__
        payload["error_summary"] = _safe_error_summary(error)
    stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _safe_error_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "trace payload validation failed"
    return redact_trace_text(str(error or error.__class__.__name__))[:500]
