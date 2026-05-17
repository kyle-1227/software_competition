from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.trace import Trace
from app.services.tracing.serializers import (
    resolve_capture_mode,
    sanitize_trace_dict,
    sanitize_trace_value,
)

_PREVIEW_LIMIT = 120
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "deepseek_api_key",
    "siliconflow_api_key",
    "access_token",
    "refresh_token",
    "bearer_token",
    "auth_token",
    "reasoning",
    "thinking",
    "chain_of_thought",
    "reasoning_content",
}
_SCRIPT_KEYS = {"script", "code", "command"}


def find_trace_by_id(
    trace_store: Any | None,
    trace_id: str,
    trace_file: Path | None = None,
) -> Trace | None:
    get_trace_tree = getattr(trace_store, "get_trace_tree", None)
    if callable(get_trace_tree):
        trace = get_trace_tree(trace_id)
        if trace is not None:
            return trace

    resolved_file = trace_file or _default_trace_file(trace_store)
    return load_trace_from_jsonl(resolved_file, trace_id) if resolved_file else None


def load_trace_from_jsonl(trace_file: Path, trace_id: str) -> Trace | None:
    for trace in iter_traces_from_jsonl(trace_file):
        if trace.trace_id == trace_id:
            return trace
    return None


def iter_traces_from_jsonl(trace_file: Path) -> Iterator[Trace]:
    path = _as_jsonl_file(trace_file)
    if path is None or not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    yield Trace.model_validate(data)
                except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
                    continue
    except OSError:
        return


def sanitize_trace_for_export(trace: Any) -> dict[str, Any]:
    if trace is None:
        return {}
    data = trace.model_dump(mode="json") if hasattr(trace, "model_dump") else trace
    sanitized = sanitize_trace_dict(data if isinstance(data, dict) else {})
    return _strict_export_value(sanitized, "")


def sanitize_span_for_export(span: Any) -> dict[str, Any]:
    if span is None:
        return {}
    data = span.model_dump(mode="json") if hasattr(span, "model_dump") else span
    sanitized = sanitize_trace_dict(data if isinstance(data, dict) else {})
    return _strict_export_value(sanitized, "")


def _default_trace_file(trace_store: Any | None) -> Path:
    storage = getattr(trace_store, "storage_path", None) or getattr(
        trace_store, "_storage_path", None
    )
    if storage is None:
        storage_path_str = getattr(settings, "trace_storage_path", "../data/traces")
        storage = Path(storage_path_str)
        if not storage.is_absolute():
            storage = Path(__file__).resolve().parents[4] / storage_path_str
    storage_path = Path(storage)
    return storage_path if storage_path.name == "traces.jsonl" else storage_path / "traces.jsonl"


def _as_jsonl_file(path: Path | None) -> Path | None:
    if path is None:
        return None
    path = Path(path)
    return path / "traces.jsonl" if path.exists() and path.is_dir() else path


def _strict_export_value(value: Any, key: str) -> Any:
    normalized = key.lower()
    if _is_sensitive_key(normalized):
        return "[REDACTED]"
    if normalized in {"answer", "prompt", "question"}:
        return _text_summary(value, normalized)
    if _is_script_key(normalized):
        return _script_summary(value, normalized)
    if normalized == "evidence":
        return _evidence_summary(value)
    if isinstance(value, Mapping):
        return {
            str(child_key): _strict_export_value(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_strict_export_value(item, normalized) for item in value[:20]]
    if isinstance(value, str):
        return _redact_text(value)
    return sanitize_trace_value(value, key)


def _text_summary(value: Any, key: str) -> dict[str, Any]:
    mode = resolve_capture_mode()
    if isinstance(value, Mapping):
        existing_hash = value.get(f"{key}_hash") or value.get("answer_hash")
        existing_length = value.get(f"{key}_length") or value.get("answer_length")
        existing_preview = (
            value.get(f"{key}_preview")
            or value.get("answer_preview")
            or value.get("prompt_preview")
            or value.get("question_preview")
        )
        if existing_hash or existing_length is not None:
            summary: dict[str, Any] = {}
            if existing_hash:
                summary[f"{key}_hash"] = existing_hash
            if existing_length is not None:
                summary[f"{key}_length"] = existing_length
            if mode != "minimal" and existing_preview:
                summary[f"{key}_preview"] = _preview(_redact_text(str(existing_preview)))
            return summary
    text = _text_from_sanitized_summary(value)
    redacted = _redact_text(text)
    summary = {
        f"{key}_hash": sha256(redacted.encode("utf-8")).hexdigest(),
        f"{key}_length": len(redacted),
    }
    if mode != "minimal":
        summary[f"{key}_preview"] = _preview(redacted)
    return summary


def _script_summary(value: Any, key: str) -> dict[str, Any]:
    mode = resolve_capture_mode()
    if isinstance(value, Mapping):
        existing_hash = (
            value.get(f"{key}_hash")
            or value.get("script_hash")
            or value.get("code_hash")
            or value.get("command_hash")
        )
        existing_preview = (
            value.get(f"{key}_preview")
            or value.get("script_preview")
            or value.get("code_preview")
            or value.get("command_preview")
        )
        existing_length = value.get(f"{key}_length") or value.get("script_length")
        if existing_hash or existing_preview or existing_length is not None:
            summary: dict[str, Any] = {}
            if existing_hash:
                summary[f"{key}_hash"] = existing_hash
            if mode != "minimal" and existing_preview:
                summary[f"{key}_preview"] = _preview(_redact_text(str(existing_preview)))
            if existing_length is not None:
                summary[f"{key}_length"] = existing_length
            return summary

    text = _redact_text(str(value or ""))
    summary = {
        f"{key}_hash": sha256(text.encode("utf-8")).hexdigest(),
        f"{key}_length": len(text),
    }
    if mode != "minimal":
        summary[f"{key}_preview"] = _preview(text)
    return summary


def _evidence_summary(value: Any) -> dict[str, Any]:
    mode = resolve_capture_mode()
    if isinstance(value, Mapping):
        summary = {
            "evidence_count": value.get("evidence_count", 0),
            "retrieved_pages": value.get("retrieved_pages", []),
        }
        if mode != "minimal":
            summary["top_snippet_preview"] = _preview(
                _redact_text(str(value.get("top_snippet_preview") or ""))
            )
        return summary
    if not isinstance(value, list):
        return {"evidence_count": 0, "retrieved_pages": []}

    pages: list[Any] = []
    top_preview = ""
    count = 0
    for item in value:
        if not isinstance(item, Mapping):
            continue
        count += 1
        page = item.get("page")
        if page is not None and page not in pages:
            pages.append(page)
        if not top_preview:
            top_preview = _preview(_redact_text(str(item.get("snippet") or "")))
    summary = {
        "evidence_count": count,
        "retrieved_pages": pages[:20],
    }
    if mode != "minimal":
        summary["top_snippet_preview"] = top_preview
    return summary


def _text_from_sanitized_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        preview = (
            value.get("answer_preview")
            or value.get("prompt_preview")
            or value.get("question_preview")
        )
        if preview:
            return str(preview)
    return str(value or "")


def _is_sensitive_key(key: str) -> bool:
    return key in _SENSITIVE_KEYS or any(
        token in key
        for token in (
            "api_key",
            "token",
            "password",
            "authorization",
            "secret",
            "reasoning",
            "thinking",
            "chain_of_thought",
            "reasoning_content",
        )
    )


def _is_script_key(key: str) -> bool:
    return (
        key in _SCRIPT_KEYS
        or "script" in key
        or key in {"source_code", "generated_code", "shell_command"}
    )


def _preview(value: str, limit: int = _PREVIEW_LIMIT) -> str:
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in (
        r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+",
        r"(?i)(access[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(refresh[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(bearer[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(auth[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(authorization\s*[:=]\s*)[^\s,;]+",
        r"(?i)(password\s*[:=]\s*)[^\s,;]+",
        r"(?i)(secret\s*[:=]\s*)[^\s,;]+",
    ):
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted
