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
    )
    span_ctx = TraceSpanContext(span)
    try:
        yield span_ctx
        span.status = SpanStatus.OK
    except Exception as exc:
        span.status = SpanStatus.ERROR
        span.error = str(exc)[:500]
        raise
    finally:
        span.end_time = datetime.now(timezone.utc)
        add_span = getattr(trace_store, "add_span", None)
        if callable(add_span):
            add_span(trace_id, span, parent_span_id)
