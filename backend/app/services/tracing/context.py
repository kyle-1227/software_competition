from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from app.schemas.trace import SpanKind, SpanStatus, TraceSpan
from app.services.tracing.serializers import sanitize_trace_dict


class TraceSpanContext:
    def __init__(self, span: TraceSpan | None = None) -> None:
        self.span = span

    def set_outputs(self, outputs: dict[str, Any] | None) -> None:
        if self.span is not None:
            self.span.outputs = sanitize_trace_dict(outputs or {})

    def set_metadata(self, metadata: dict[str, Any] | None) -> None:
        if self.span is not None:
            self.span.metadata = sanitize_trace_dict(metadata or {})

    def add_metadata(self, key: str, value: Any) -> None:
        if self.span is not None:
            sanitized = sanitize_trace_dict({str(key): value})
            self.span.metadata[str(key)] = sanitized[str(key)]


@asynccontextmanager
async def trace_span(
    trace_store: Any,
    trace_id: str | None,
    name: str,
    kind: SpanKind,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    parent_span_id: str | None = None,
    attempt: int | None = None,
    retry_count: int | None = None,
    fallback_used: bool | None = None,
    degraded: bool | None = None,
    token_usage: dict[str, Any] | None = None,
    cost_estimate: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
):
    if trace_store is None or not trace_id:
        yield TraceSpanContext()
        return

    span = TraceSpan(
        name=name,
        kind=kind,
        parent_span_id=parent_span_id,
        start_time=datetime.now(timezone.utc),
        inputs=sanitize_trace_dict(inputs or {}),
        metadata=sanitize_trace_dict(metadata or {}),
        attempt=attempt,
        retry_count=retry_count,
        fallback_used=bool(fallback_used),
        degraded=bool(degraded),
        token_usage=sanitize_trace_dict(token_usage or {}) if token_usage else None,
        cost_estimate=sanitize_trace_dict(cost_estimate or {}) if cost_estimate else None,
        quality=sanitize_trace_dict(quality or {}) if quality else None,
    )
    span_ctx = TraceSpanContext(span)
    try:
        yield span_ctx
        span.status = SpanStatus.OK
    except Exception as exc:
        span.status = SpanStatus.ERROR
        span.error = str(exc)[:500]
        span.error_type = exc.__class__.__name__
        raise
    finally:
        span.end_time = datetime.now(timezone.utc)
        add_span = getattr(trace_store, "add_span", None)
        if callable(add_span):
            add_span(trace_id, span, parent_span_id)
