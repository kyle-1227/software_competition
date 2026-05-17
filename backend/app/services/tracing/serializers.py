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
TEXT_SUMMARY_KEYS = {"answer", "prompt", "question"}
MAX_STRING_LENGTH = 500
MAX_PREVIEW_LENGTH = 120
DEBUG_PREVIEW_LENGTH = 500
MAX_LIST_LENGTH = 20
MAX_DICT_KEYS = 50


def sanitize_trace_value(
    value: Any,
    key: str | None = None,
    *,
    capture_mode: str | None = None,
) -> Any:
    mode = resolve_capture_mode(capture_mode)
    normalized_key = str(key or "").lower()
    if _is_sensitive_key(normalized_key):
        return "[REDACTED]"
    if normalized_key in TEXT_SUMMARY_KEYS:
        return _summarize_text(value, normalized_key, mode)
    if normalized_key == "evidence":
        return _summarize_evidence(value, mode)
    if normalized_key in SCRIPT_KEYS:
        return _summarize_script(value, normalized_key, mode)
    if isinstance(value, Mapping):
        return sanitize_trace_dict(dict(value), capture_mode=mode)
    if isinstance(value, list):
        return _sanitize_list(value, mode)
    if isinstance(value, tuple):
        return _sanitize_list(list(value), mode)
    if isinstance(value, str):
        return _truncate_string(value, _string_limit_for_mode(mode))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_string(str(value), _string_limit_for_mode(mode))


def sanitize_trace_dict(
    data: dict[str, Any] | None,
    *,
    capture_mode: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    mode = resolve_capture_mode(capture_mode)
    sanitized: dict[str, Any] = {}
    items = list(data.items())
    for key, value in items[:MAX_DICT_KEYS]:
        sanitized[str(key)] = sanitize_trace_value(value, str(key), capture_mode=mode)
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


def resolve_capture_mode(mode: str | None = None) -> str:
    if mode in {"minimal", "summary", "debug"}:
        return str(mode)
    try:
        from app.core.config import settings

        configured = getattr(settings, "trace_capture_mode", "summary")
    except Exception:  # pragma: no cover - defensive during early imports
        configured = "summary"
    return configured if configured in {"minimal", "summary", "debug"} else "summary"


def _sanitize_list(items: list[Any], mode: str) -> list[Any]:
    sanitized = [
        sanitize_trace_value(item, capture_mode=mode) for item in items[:MAX_LIST_LENGTH]
    ]
    if len(items) > MAX_LIST_LENGTH:
        sanitized.append({"truncated": True, "original_length": len(items)})
    return sanitized


def _truncate_string(value: str, limit: int = MAX_STRING_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def _preview(value: str, limit: int = MAX_PREVIEW_LENGTH) -> str:
    return value[:limit] + ("..." if len(value) > limit else "")


def _summarize_text(value: Any, key: str, mode: str) -> dict[str, Any]:
    text = str(value or "")
    summary = {
        f"{key}_hash": sha256(text.encode("utf-8")).hexdigest(),
        f"{key}_length": len(text),
    }
    if key == "answer":
        summary = {
            "answer_hash": summary["answer_hash"],
            "answer_length": summary["answer_length"],
        }
    if mode != "minimal":
        preview_limit = DEBUG_PREVIEW_LENGTH if mode == "debug" else MAX_PREVIEW_LENGTH
        preview_key = "answer_preview" if key == "answer" else f"{key}_preview"
        summary[preview_key] = _preview(text, preview_limit)
    return summary


def _summarize_script(value: Any, key: str, mode: str) -> dict[str, Any]:
    if isinstance(value, dict):
        summary: dict[str, Any] = {}
        if value.get(f"{key}_hash"):
            summary[f"{key}_hash"] = value.get(f"{key}_hash")
        if mode != "minimal" and value.get(f"{key}_preview"):
            summary[f"{key}_preview"] = _preview(str(value.get(f"{key}_preview")))
        if value.get("script_hash"):
            summary["script_hash"] = value.get("script_hash")
        if mode != "minimal" and value.get("script_preview"):
            summary["script_preview"] = _preview(str(value.get("script_preview")))
        if summary:
            return summary
        text = str(value)
    else:
        text = str(value or "")
    summary = {
        f"{key}_hash": sha256(text.encode("utf-8")).hexdigest(),
        f"{key}_length": len(text),
    }
    if mode != "minimal":
        preview_limit = DEBUG_PREVIEW_LENGTH if mode == "debug" else MAX_PREVIEW_LENGTH
        summary[f"{key}_preview"] = _preview(text, preview_limit)
    return summary


def _summarize_evidence(value: Any, mode: str) -> dict[str, Any]:
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
    summary = {
        "evidence_count": len([item for item in value if isinstance(item, dict)]),
        "retrieved_pages": pages[:MAX_LIST_LENGTH],
    }
    if mode != "minimal":
        summary["top_snippet_preview"] = preview
    return summary


def _is_sensitive_key(key: str) -> bool:
    if key in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}:
        return False
    return key in SENSITIVE_KEYS or any(
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


def _string_limit_for_mode(mode: str) -> int:
    if mode == "debug":
        return DEBUG_PREVIEW_LENGTH
    if mode == "minimal":
        return MAX_PREVIEW_LENGTH
    return MAX_STRING_LENGTH
