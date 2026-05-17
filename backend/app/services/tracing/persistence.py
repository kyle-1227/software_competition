from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any

from app.schemas.trace import Trace, TraceSpan
from app.services.tracing.serializers import (
    redact_trace_text,
    resolve_capture_mode,
    sanitize_trace_dict,
)

_SUMMARY_PREVIEW_LIMIT = 120
_DEBUG_PREVIEW_LIMIT = 500


def question_persistence_fields(
    question: str | None,
    capture_mode: str | None = None,
) -> dict[str, Any]:
    mode = resolve_capture_mode(capture_mode)
    text = str(question or "")
    preview = None
    if mode != "minimal":
        preview = _preview(redact_trace_text(text), _preview_limit(mode))
    return {
        "question_hash": sha256(text.encode("utf-8")).hexdigest() if text else None,
        "question_preview": preview,
        "question_length": len(text),
        "question": preview,
    }


def sanitize_trace_for_persistence(
    trace: Trace,
    capture_mode: str | None = None,
) -> dict[str, Any]:
    mode = resolve_capture_mode(capture_mode)
    question_fields = question_persistence_fields(getattr(trace, "question", None), mode)
    return {
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "session_id": trace.session_id,
        "user_id": trace.user_id,
        **question_fields,
        "normalized_question": _safe_preview_or_none(trace.normalized_question, mode),
        "app_env": trace.app_env,
        "app_version": trace.app_version,
        "git_commit": trace.git_commit,
        "llm_provider": trace.llm_provider,
        "llm_model": trace.llm_model,
        "embedding_model": trace.embedding_model,
        "reranker_model": trace.reranker_model,
        "manual_id": trace.manual_id,
        "index_version": trace.index_version,
        "index_sha256": trace.index_sha256,
        "feature_flags": sanitize_trace_dict(trace.feature_flags, capture_mode=mode),
        "status": _enum_value(trace.status),
        "final_answer_hash": trace.final_answer_hash,
        "created_at": _json_value(trace.created_at),
        "closed_at": _json_value(trace.closed_at),
        "total_duration_ms": trace.total_duration_ms,
        "root_span": sanitize_span_for_persistence(trace.root_span, mode),
    }


def sanitize_span_for_persistence(
    span: TraceSpan,
    capture_mode: str | None = None,
) -> dict[str, Any]:
    mode = resolve_capture_mode(capture_mode)
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "kind": _enum_value(span.kind),
        "start_time": _json_value(span.start_time),
        "end_time": _json_value(span.end_time),
        "duration_ms": span.duration_ms,
        "inputs": sanitize_trace_dict(span.inputs, capture_mode=mode),
        "outputs": sanitize_trace_dict(span.outputs, capture_mode=mode),
        "status": _enum_value(span.status),
        "error": _safe_preview_or_none(span.error, mode),
        "error_type": span.error_type,
        "attempt": span.attempt,
        "retry_count": span.retry_count,
        "fallback_used": span.fallback_used,
        "degraded": span.degraded,
        "token_usage": sanitize_trace_dict(span.token_usage, capture_mode=mode)
        if span.token_usage
        else None,
        "cost_estimate": sanitize_trace_dict(span.cost_estimate, capture_mode=mode)
        if span.cost_estimate
        else None,
        "quality": sanitize_trace_dict(span.quality, capture_mode=mode)
        if span.quality
        else None,
        "metadata": sanitize_trace_dict(span.metadata, capture_mode=mode),
        "children": [
            sanitize_span_for_persistence(child, mode)
            for child in getattr(span, "children", []) or []
        ],
    }


def _safe_preview_or_none(value: Any, mode: str) -> str | None:
    if value is None:
        return None
    if mode == "minimal":
        return None
    return _preview(redact_trace_text(str(value)), _preview_limit(mode))


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _preview_limit(mode: str) -> int:
    return _DEBUG_PREVIEW_LIMIT if mode == "debug" else _SUMMARY_PREVIEW_LIMIT


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
