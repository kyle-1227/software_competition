from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.trace import SpanKind, Trace, TraceSpan


def trace_helper(
    trace_id: str,
    *,
    status: str = "success",
    closed_at: datetime | None = None,
    question: str = "q",
    session_id: str = "session",
    degraded: bool = False,
    fallback_used: bool = False,
    span_name: str = "node.work",
    duration_ms: float | None = None,
    span_inputs: dict[str, Any] | None = None,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    if closed_at is None:
        resolved_closed = None
        resolved_created = started
    else:
        resolved_closed = closed_at
        resolved_created = closed_at - timedelta(seconds=1)
    if span_inputs is not None or span_name != "node.work" or duration_ms is not None:
        span = TraceSpan(
            name=span_name,
            kind=SpanKind.NODE,
            degraded=degraded,
            fallback_used=fallback_used,
            metadata={"degraded": degraded, "fallback_used": fallback_used},
            inputs=span_inputs or {},
            duration_ms=duration_ms,
        )
    else:
        span = TraceSpan(
            name=span_name,
            kind=SpanKind.NODE,
            degraded=degraded,
            fallback_used=fallback_used,
            metadata={"degraded": degraded, "fallback_used": fallback_used},
        )
    root = TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span], duration_ms=duration_ms)
    return Trace(
        trace_id=trace_id,
        session_id=session_id,
        question=question,
        status=status,
        created_at=resolved_created,
        closed_at=resolved_closed,
        total_duration_ms=duration_ms,
        root_span=root,
    )


class FakeRepository:
    def __init__(self, traces: list[Trace]) -> None:
        self.traces: dict[str, Trace] = {t.trace_id: t for t in traces}

    def list_traces(self, limit: int = 50, session_id: str | None = None, status: str | None = None) -> list[Trace]:
        traces = list(self.traces.values())
        if session_id:
            traces = [t for t in traces if t.session_id == session_id]
        if status:
            traces = [t for t in traces if str(t.status.value) == status]
        if limit is not None:
            traces = traces[:limit]
        return traces

    def get_trace(self, trace_id: str) -> Trace | None:
        return self.traces.get(trace_id)
