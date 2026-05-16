from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.query import EvaluationResult, QueryRequest
from app.schemas.trace import SpanKind, SpanStatus
from app.services.agent_harness_lc import AgentHarness
from app.services.agent_loop.policy import AgentLoopPolicy
from app.services.answer_generation import draft_answer_with_llm
from app.services.evaluator_optimizer import EvaluatorOptimizer
from app.services.graph.graph_builder import _run_manual_lookup_with_retry
from app.services.tool_registry import ToolResult
from app.services.trace_store import TraceStore
from app.services.tracing.context import trace_span
from app.services.tracing.serializers import sanitize_trace_dict


@pytest.mark.anyio
async def test_trace_span_records_success(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()

    async with trace_span(
        store,
        trace_id,
        "node.test",
        SpanKind.NODE,
        inputs={"question": "q"},
    ) as span:
        span.set_outputs({"answer": "hello"})

    recorded = _find_span(store.get_trace_tree(trace_id), "node.test")
    assert recorded is not None
    assert recorded.status == SpanStatus.OK
    assert recorded.outputs["answer"]["answer_length"] == 5
    assert "duration_ms" in recorded.metadata


@pytest.mark.anyio
async def test_trace_span_records_error(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()

    with pytest.raises(RuntimeError):
        async with trace_span(store, trace_id, "node.error", SpanKind.NODE):
            raise RuntimeError("boom")

    recorded = _find_span(store.get_trace_tree(trace_id), "node.error")
    assert recorded is not None
    assert recorded.status == SpanStatus.ERROR
    assert recorded.error == "boom"


@pytest.mark.anyio
async def test_trace_span_noop_context_methods_are_safe() -> None:
    async with trace_span(None, None, "node.noop", SpanKind.NODE) as span:
        span.set_outputs({"answer": "ok"})
        span.set_metadata({"api_key": "real-api-key"})
        span.add_metadata("access_token", "secret-token")


def test_trace_sanitizer_redacts_sensitive_fields_and_keeps_usage_tokens() -> None:
    sanitized = sanitize_trace_dict(
        {
            "api_key": "real-api-key",
            "password": "pw",
            "secret": "secret-value",
            "access_token": "secret-token",
            "reasoning": "reasoning content",
            "chain_of_thought": "hidden",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        }
    )

    dumped = repr(sanitized)
    assert "real-api-key" not in dumped
    assert "secret-token" not in dumped
    assert "reasoning content" not in dumped
    assert sanitized["usage"]["input_tokens"] == 10
    assert sanitized["usage"]["output_tokens"] == 5
    assert sanitized["usage"]["total_tokens"] == 15


def test_get_trace_tree_reads_active_and_closed_trace(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()

    assert store.get_trace_tree(trace_id) is not None
    closed = store.close_trace(trace_id)
    assert closed is not None
    assert store.get_trace_session(trace_id) is None
    assert store.get_trace_tree(trace_id) is closed


@pytest.mark.anyio
async def test_graph_records_core_node_spans(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    harness = AgentHarness(trace_store=store)

    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办？", device_name="摩托车发动机")
    )

    tree = store.get_trace_tree(response.trace_id or "")
    span_names = _span_names(tree)
    assert {
        "node.input_guardrail",
        "node.orchestrator",
        "node.worker_executor",
        "node.evaluator_optimizer",
        "node.trace",
        "node.memory_save",
        "node.finalize",
    }.issubset(span_names)


@pytest.mark.anyio
async def test_manual_lookup_records_tool_span(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    services = SimpleNamespace(
        trace_store=store,
        tool_registry=_ManualLookupRegistry(),
        agent_loop_policy=AgentLoopPolicy(max_tool_retries=5),
    )

    await _run_manual_lookup_with_retry(
        {
            "trace_id": trace_id,
            "question": "火花塞间隙是多少？",
            "device_name": "摩托车发动机",
            "tool_calls": [],
            "evidence": [],
            "warnings": [],
            "degradation_events": [],
        },
        services,
    )

    span = _find_span(store.get_trace_tree(trace_id), "tool.manual_lookup")
    assert span is not None
    assert span.metadata["attempts"] == 1
    assert span.metadata["degraded"] is False
    assert span.metadata["placeholder_used"] is False


@pytest.mark.anyio
async def test_answer_generation_records_llm_span(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    services = SimpleNamespace(trace_store=store, llm_client=None)

    await draft_answer_with_llm(
        services,
        {"trace_id": trace_id, "question": "q", "evidence": [], "tool_calls": []},
    )

    span = _find_span(store.get_trace_tree(trace_id), "llm.answer_generation")
    assert span is not None
    assert span.outputs["answer_length"] > 0
    assert "answer" not in span.outputs


@pytest.mark.anyio
async def test_evaluator_optimizer_records_span(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    services = SimpleNamespace(
        trace_store=store,
        llm_client=None,
        tool_registry=_ComplianceRegistry(),
    )
    optimizer = EvaluatorOptimizer(evaluator=_AsyncEvaluator())

    await optimizer.generate_and_evaluate(
        {"trace_id": trace_id, "question": "q", "evidence": [], "tool_calls": []},
        services,
    )

    span = _find_span(store.get_trace_tree(trace_id), "evaluator.optimizer")
    assert span is not None
    assert span.metadata["iteration_count"] >= 1
    assert span.metadata["final_confidence"] == 0.9
    assert span.metadata["issues_count"] == 0


@pytest.mark.anyio
async def test_trace_does_not_include_api_key_or_reasoning(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()

    long_answer = "answer-" * 80
    async with trace_span(
        store,
        trace_id,
        "node.safe",
        SpanKind.NODE,
        inputs={
            "api_key": "real-api-key",
            "access_token": "secret-token",
            "reasoning": "reasoning content",
            "script": "print('hello')",
        },
        metadata={"chain_of_thought": "chain-of-thought content"},
    ) as span:
        span.set_outputs({"answer": long_answer})

    trace = store.close_trace(trace_id)
    dumped = trace.model_dump_json() if trace is not None else ""
    assert "real-api-key" not in dumped
    assert "secret-token" not in dumped
    assert "reasoning content" not in dumped
    assert "chain-of-thought content" not in dumped
    assert long_answer not in dumped


class _ManualLookupRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(
            tool_name=name,
            success=True,
            data=[
                {
                    "source": "manual",
                    "page": 3,
                    "snippet": "火花塞间隙标准值 0.7～0.9 mm",
                    "metadata": {"chunk_id": "chunk-1"},
                }
            ],
            metadata={"duration_ms": 1},
        )


class _ComplianceRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(
            tool_name=name,
            success=True,
            data={"is_safe": True},
            metadata={"duration_ms": 1},
        )


class _AsyncEvaluator:
    async def evaluate(self, answer, evidence, tool_calls, sop):
        del answer, evidence, tool_calls, sop
        return EvaluationResult(
            is_safe=True,
            is_compliant=True,
            confidence=0.9,
            issues=[],
        )


def _span_names(trace) -> set[str]:
    if trace is None:
        return set()
    return {span.name for span in _walk_spans(trace.root_span)}


def _find_span(trace, name: str):
    if trace is None:
        return None
    for span in _walk_spans(trace.root_span):
        if span.name == name:
            return span
    return None


def _walk_spans(span):
    yield span
    for child in span.children:
        yield from _walk_spans(child)
