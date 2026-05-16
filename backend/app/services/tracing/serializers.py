from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

SENSITIVE_KEYS = {
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
SCRIPT_KEYS = {"script", "code", "command"}
MAX_STRING_LENGTH = 500
MAX_PREVIEW_LENGTH = 120
MAX_LIST_LENGTH = 20
MAX_DICT_KEYS = 50


def sanitize_trace_value(value: Any, key: str | None = None) -> Any:
    normalized_key = str(key or "").lower()
    if normalized_key in SENSITIVE_KEYS:
        return "[REDACTED]"
    if normalized_key == "evidence":
        return _summarize_evidence(value)
    if normalized_key == "answer":
        return _summarize_answer(value)
    if normalized_key in SCRIPT_KEYS:
        return _summarize_script(value, normalized_key)
    if isinstance(value, Mapping):
        return sanitize_trace_dict(dict(value))
    if isinstance(value, list):
        return _sanitize_list(value)
    if isinstance(value, tuple):
        return _sanitize_list(list(value))
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_string(str(value))


def sanitize_trace_dict(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    sanitized: dict[str, Any] = {}
    items = list(data.items())
    for key, value in items[:MAX_DICT_KEYS]:
        sanitized[str(key)] = sanitize_trace_value(value, str(key))
    if len(items) > MAX_DICT_KEYS:
        sanitized["truncated"] = True
        sanitized["original_key_count"] = len(items)
    return sanitized


def summarize_inputs(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return sanitize_trace_dict(value)
    return {"value": sanitize_trace_value(value)}


def summarize_outputs(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return sanitize_trace_dict(value)
    return {"value": sanitize_trace_value(value)}


def _sanitize_list(items: list[Any]) -> list[Any]:
    sanitized = [sanitize_trace_value(item) for item in items[:MAX_LIST_LENGTH]]
    if len(items) > MAX_LIST_LENGTH:
        sanitized.append({"truncated": True, "original_length": len(items)})
    return sanitized


def _truncate_string(value: str, limit: int = MAX_STRING_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def _preview(value: str, limit: int = MAX_PREVIEW_LENGTH) -> str:
    return value[:limit] + ("..." if len(value) > limit else "")


def _summarize_answer(value: Any) -> dict[str, Any]:
    text = str(value or "")
    return {
        "answer_length": len(text),
        "answer_preview": _preview(text),
    }


def _summarize_script(value: Any, key: str) -> dict[str, Any]:
    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        if value.get(f"{key}_hash"):
            summary[f"{key}_hash"] = value.get(f"{key}_hash")
        if value.get(f"{key}_preview"):
            summary[f"{key}_preview"] = _preview(str(value.get(f"{key}_preview")))
        if value.get("script_hash"):
            summary["script_hash"] = value.get("script_hash")
        if value.get("script_preview"):
            summary["script_preview"] = _preview(str(value.get("script_preview")))
        if summary:
            return summary
        text = str(value)
    else:
        text = str(value or "")
    return {
        f"{key}_hash": sha256(text.encode("utf-8")).hexdigest(),
        f"{key}_preview": _preview(text),
        f"{key}_length": len(text),
    }


def _summarize_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {"evidence_count": 0, "retrieved_pages": []}
    pages: list[Any] = []
    preview = ""
    for item in value:
        if not isinstance(item, dict):
            continue
        page = item.get("page")
        if page is not None and page not in pages:
            pages.append(page)
        if not preview:
            preview = _preview(str(item.get("snippet") or ""))
    return {
        "evidence_count": len([item for item in value if isinstance(item, dict)]),
        "retrieved_pages": pages[:MAX_LIST_LENGTH],
        "top_snippet_preview": preview,
    }
