from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from app.services.tracing.serializers import sanitize_trace_dict, sanitize_trace_value

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


def span_count_items(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def summarize_worker_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return sanitize_trace_dict(
            {
                "evidence_count": 0,
                "tool_call_count": 0,
                "warning_count": 0,
                "degradation_event_count": 0,
                "has_ai_coding": False,
                "has_sandbox_result": False,
                "requires_human_approval": False,
                "degraded": False,
            }
        )
    return sanitize_trace_dict(
        {
            "evidence_count": span_count_items(result.get("evidence")),
            "tool_call_count": span_count_items(result.get("tool_calls")),
            "warning_count": span_count_items(result.get("warnings")),
            "degradation_event_count": span_count_items(
                result.get("degradation_events")
            ),
            "has_ai_coding": result.get("ai_coding") is not None,
            "has_sandbox_result": result.get("sandbox_result") is not None,
            "requires_human_approval": bool(
                result.get("requires_human_approval", False)
            ),
            "degraded": bool(result.get("degraded", False)),
        }
    )


def summarize_tool_result(result: Any) -> dict[str, Any]:
    data = _get_attr_or_key(result, "data")
    metadata = _get_attr_or_key(result, "metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    success = bool(_get_attr_or_key(result, "success", False))
    status = _get_attr_or_key(result, "status")
    if status is None:
        status = "success" if success else "failed"
    error = _get_attr_or_key(result, "error")
    output_type, output_count = _output_summary(data)
    return sanitize_trace_dict(
        {
            "success": success,
            "status": status,
            "duration_ms": metadata.get("duration_ms")
            or _get_attr_or_key(result, "duration_ms"),
            "degraded": bool(metadata.get("degraded", False))
            or bool(_get_attr_or_key(result, "degraded", False)),
            "error_preview": _preview(error),
            "output_type": output_type,
            "output_count": output_count,
        }
    )


def summarize_retrieval_result(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    items = [item for item in evidence if isinstance(item, dict)]
    pages: list[Any] = []
    for item in items:
        page = item.get("page")
        if page is not None and page not in pages:
            pages.append(page)

    top = items[0] if items else {}
    top_metadata = top.get("metadata") if isinstance(top.get("metadata"), dict) else {}
    placeholder_used = any(
        isinstance(item.get("metadata"), dict)
        and item["metadata"].get("retriever")
        in {"llama-index-placeholder", "manual_lookup-degraded"}
        for item in items
    )
    return sanitize_trace_dict(
        {
            "evidence_count": len(items),
            "retrieved_pages": pages,
            "placeholder_used": placeholder_used,
            "top_source": top.get("source"),
            "top_chunk_id": top_metadata.get("chunk_id"),
            "top_snippet_preview": _preview(top.get("snippet")),
        }
    )


def summarize_span_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return sanitize_trace_dict(
        {str(key): _strict_summarize_value(value, str(key)) for key, value in payload.items()}
    )


def _strict_summarize_value(value: Any, key: str) -> Any:
    normalized = key.lower()
    if normalized in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if normalized == "answer":
        text = str(value or "")
        return {
            "answer_length": len(text),
            "answer_hash": sha256(text.encode("utf-8")).hexdigest(),
        }
    if _is_script_key(normalized):
        text = str(value or "")
        return {
            f"{normalized}_hash": sha256(text.encode("utf-8")).hexdigest(),
            f"{normalized}_preview": _preview(text),
            f"{normalized}_length": len(text),
        }
    if normalized == "evidence" and isinstance(value, list):
        return summarize_retrieval_result([item for item in value if isinstance(item, dict)])
    if isinstance(value, Mapping):
        return {
            str(child_key): _strict_summarize_value(child_value, str(child_key))
            for child_key, child_value in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [sanitize_trace_value(item) for item in value[:20]]
    return sanitize_trace_value(value, key)


def _get_attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _output_summary(value: Any) -> tuple[str, int | None]:
    if value is None:
        return "none", 0
    if isinstance(value, list):
        return "list", len(value)
    if isinstance(value, dict):
        return "dict", len(value)
    if isinstance(value, str):
        return "str", 1
    return type(value).__name__, None


def _is_script_key(key: str) -> bool:
    return (
        key in _SCRIPT_KEYS
        or "script" in key
        or key in {"source_code", "generated_code", "shell_command"}
    )


def _preview(value: Any, limit: int = _PREVIEW_LIMIT) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit].rstrip() + "..."
