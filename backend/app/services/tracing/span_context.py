from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from app.schemas.trace import SpanKind, SpanStatus, TraceSpan


@asynccontextmanager
async def trace_span(
    trace_store: Any,
    trace_id: str,
    name: str,
    kind: SpanKind,
    parent_span_id: str | None = None,
):
    """Async context manager that wraps a graph node with a TraceSpan.

    Usage:
        async with trace_span(services.trace_store, trace_id, "retrieval", SpanKind.TOOL) as span:
            span.inputs = {"question": question}
            result = await do_work()
            span.outputs = {"evidence_count": len(result)}
    """
    span = TraceSpan(
        name=name,
        kind=kind,
        parent_span_id=parent_span_id,
        start_time=datetime.now(timezone.utc),
    )
    try:
        yield span
        span.status = SpanStatus.OK
    except Exception as exc:
        span.status = SpanStatus.ERROR
        span.error = str(exc)
        raise
    finally:
        span.end_time = datetime.now(timezone.utc)
        if hasattr(trace_store, "add_span"):
            trace_store.add_span(trace_id, span, parent_span_id)
