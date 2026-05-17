from __future__ import annotations

import pytest

from app.schemas.trace import SpanKind
from app.services.trace_store import TraceStore
from app.services.tracing.context import trace_span


@pytest.mark.anyio
async def test_trace_span_records_ok_production_fields(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace(session_id="s1", question="q")

    async with trace_span(
        store,
        trace_id,
        "tool.manual_lookup.attempt",
        SpanKind.TOOL,
        inputs={"api_key": "secret-value", "question": "q"},
        attempt=2,
        retry_count=1,
        fallback_used=True,
        degraded=True,
        token_usage={"input_tokens": 10},
        cost_estimate={"usd": 0.01},
        quality={"confidence": 0.8},
    ):
        pass

    trace = store.close_trace(trace_id)
    span = trace.root_span.children[0]
    assert span.duration_ms is not None
    assert span.attempt == 2
    assert span.retry_count == 1
    assert span.fallback_used is True
    assert span.degraded is True
    assert span.token_usage == {"input_tokens": 10}
    assert span.inputs["api_key"] == "[REDACTED]"


@pytest.mark.anyio
async def test_trace_span_records_error_type(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace(session_id="s1", question="q")

    with pytest.raises(ValueError):
        async with trace_span(store, trace_id, "node.fail", SpanKind.NODE):
            raise ValueError("boom")

    trace = store.close_trace(trace_id)
    span = trace.root_span.children[0]
    assert getattr(span.status, "value", span.status) == "error"
    assert span.error_type == "ValueError"
    assert trace.status == "error"
