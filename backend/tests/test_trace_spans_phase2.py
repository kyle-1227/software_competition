from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.evals.run_eval import _case_result, _trace_summary
from app.schemas.orchestrator import OrchestratorDecision
from app.schemas.query import EvaluationResult, SandboxResult
from app.services.agent_loop.retry import (
    execute_sandbox_with_retry,
    execute_tool_with_retry,
)
from app.services.evaluator_optimizer import EvaluatorOptimizer
from app.services.retriever import Retriever
from app.services.tool_registry import ToolResult
from app.services.tools.manual_lookup import ManualLookupTool
from app.services.trace_store import TraceStore
from app.services.tracing.context import trace_span
from app.schemas.trace import SpanKind
from app.services.workers.dispatcher import WorkerDispatcher


@pytest.mark.anyio
async def test_worker_dispatcher_records_worker_spans(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    result = {
        "evidence": [_evidence()],
        "tool_calls": [_tool_call("manual_lookup")],
        "warnings": ["warn"],
        "worker_outputs": [{"worker": "fault_triage"}],
    }
    dispatcher = WorkerDispatcher({"fault_triage": _FakeWorker("fault_triage", result)})

    worker_results = await dispatcher.dispatch(
        OrchestratorDecision(intent="fault_triage", workers=["fault_triage"]),
        {"trace_id": trace_id, "question": "q", "evidence": [], "tool_calls": []},
        SimpleNamespace(trace_store=store),
    )

    assert worker_results == [result]
    span = _find_span(store.get_trace_tree(trace_id), "worker.fault_triage")
    assert span is not None
    assert span.outputs["evidence_count"] == 1
    assert span.outputs["tool_call_count"] == 1


@pytest.mark.anyio
async def test_tool_retry_records_each_attempt_span(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    registry = _FlakyRegistry(
        failures=2,
        success=ToolResult(tool_name="phase2_tool", success=True, data=[]),
    )

    result = await execute_tool_with_retry(
        registry,
        "phase2_tool",
        {"question": "q"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
        trace_store=store,
        trace_id=trace_id,
    )

    spans = _find_spans(store.get_trace_tree(trace_id), "tool.phase2_tool.attempt")
    assert result.success is True
    assert len(result.tool_calls) == 3
    assert [span.metadata["attempt"] for span in spans] == [1, 2, 3]
    assert spans[-1].metadata["success"] is True


@pytest.mark.anyio
async def test_tool_retry_failed_5_attempts_records_5_spans(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()

    result = await execute_tool_with_retry(
        _AlwaysFailRegistry(),
        "phase2_tool",
        {"question": "q"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
        trace_store=store,
        trace_id=trace_id,
    )

    spans = _find_spans(store.get_trace_tree(trace_id), "tool.phase2_tool.attempt")
    assert result.degraded is True
    assert len(spans) == 5
    assert spans[-1].metadata["final_attempt"] is True


@pytest.mark.anyio
async def test_sandbox_retry_records_attempt_spans_and_sanitizes_script(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    script = "print('phase2 full script should not leak')\n" * 20

    await execute_sandbox_with_retry(
        _FailingSandbox(),
        script,
        "python",
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
        trace_store=store,
        trace_id=trace_id,
    )

    trace = store.close_trace(trace_id)
    dumped = trace.model_dump_json() if trace is not None else ""
    spans = _find_spans(trace, "sandbox.execute.attempt")
    assert len(spans) == 5
    assert script not in dumped
    assert "script_hash" in dumped


@pytest.mark.anyio
async def test_compliance_check_attempt_span_recorded(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    optimizer = EvaluatorOptimizer(evaluator=_AsyncEvaluator())
    services = SimpleNamespace(
        trace_store=store,
        llm_client=None,
        tool_registry=_ComplianceRegistry(),
    )

    await optimizer.generate_and_evaluate(
        {"trace_id": trace_id, "question": "q", "evidence": [], "tool_calls": []},
        services,
    )

    assert _find_span(store.get_trace_tree(trace_id), "tool.compliance_check.attempt")
    span = _find_span(store.get_trace_tree(trace_id), "evaluator.optimizer")
    assert span is not None
    assert span.metadata["compliance_attempts"] == 1
    assert span.metadata["compliance_success"] is True
    assert span.metadata["compliance_degraded"] is False


@pytest.mark.anyio
async def test_eval_result_contains_trace_observability_fields(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    async with trace_span(
        store,
        trace_id,
        "node.approval",
        SpanKind.NODE,
        metadata={"requires_human_approval": True},
    ):
        pass
    store.close_trace(trace_id)

    result = _case_result(
        {
            "id": "case-1",
            "question": "q",
            "expected_pages": [],
            "expected_terms": [],
        },
        [],
        "",
        None,
        trace_id=trace_id,
        trace_summary=_trace_summary(store, trace_id),
    )

    assert result["trace_id"] == trace_id
    assert result["trace_span_count"] == 1
    assert result["trace_error_count"] == 0
    assert result["trace_has_approval"] is True
    assert result["trace_has_fail_safe"] is False


@pytest.mark.anyio
async def test_trace_phase2_does_not_leak_sensitive_values(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    script = "print('phase2 sensitive script body')\n" * 20

    await execute_tool_with_retry(
        _AlwaysFailRegistry(),
        "phase2_tool",
        {
            "api_key": "real-api-key",
            "access_token": "secret-token",
            "secret": "real-secret",
            "script": script,
            "reasoning": "hidden reasoning",
        },
        max_retries=1,
        backoff_ms=[0],
        trace_store=store,
        trace_id=trace_id,
    )

    trace = store.close_trace(trace_id)
    dumped = trace.model_dump_json() if trace is not None else ""
    assert "real-api-key" not in dumped
    assert "secret-token" not in dumped
    assert "real-secret" not in dumped
    assert "hidden reasoning" not in dumped
    assert script not in dumped
    assert "script_hash" in dumped


@pytest.mark.anyio
async def test_manual_lookup_records_retriever_span(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    retriever = Retriever(
        vector_retriever=_FakeVectorRetriever([_FakeNode("chunk-1", 3, "spark plug")]),
        trace_store=store,
    )
    tool = ManualLookupTool(retriever=retriever, trace_store=store)

    await tool.run({"question": "spark plug", "trace_id": trace_id})

    assert _find_span(store.get_trace_tree(trace_id), "retriever.vector_search")


@pytest.mark.anyio
async def test_reranker_records_span_when_enabled(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    retriever = Retriever(
        top_k=1,
        vector_retriever=_FakeVectorRetriever(
            [
                _FakeNode("chunk-1", 1, "first"),
                _FakeNode("chunk-2", 2, "second"),
            ]
        ),
        reranker=_FakeReranker(),
        trace_store=store,
    )

    await retriever.search("spark", trace_id=trace_id)

    span = _find_span(store.get_trace_tree(trace_id), "reranker.score")
    assert span is not None
    assert span.metadata["candidate_count"] == 2


class _FakeWorker:
    def __init__(self, name: str, result: dict[str, Any]) -> None:
        self.name = name
        self.result = result

    async def execute(self, state: dict[str, Any], services: Any) -> dict[str, Any]:
        del state, services
        return self.result


class _FlakyRegistry:
    def __init__(self, failures: int, success: ToolResult) -> None:
        self.failures = failures
        self.success = success
        self.calls = 0

    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        self.calls += 1
        if self.calls <= self.failures:
            return ToolResult(tool_name=name, success=False, error="transient")
        return self.success


class _AlwaysFailRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=False, error="boom")


class _FailingSandbox:
    def execute(self, script: str, language: str) -> SandboxResult:
        del script
        return SandboxResult(language=language, allowed=False, error="blocked")


class _ComplianceRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=True, data={"is_safe": True})


class _AsyncEvaluator:
    async def evaluate(self, answer, evidence, tool_calls, sop):
        del answer, evidence, tool_calls, sop
        return EvaluationResult(
            is_safe=True,
            is_compliant=True,
            confidence=0.9,
            issues=[],
        )


class _FakeVectorRetriever:
    def __init__(self, nodes: list[Any]) -> None:
        self.nodes = nodes

    def retrieve(self, question: str) -> list[Any]:
        del question
        return self.nodes


class _FakeNode:
    def __init__(self, chunk_id: str, page: int, text: str) -> None:
        self.metadata = {
            "source": "manual",
            "page": page,
            "chunk_id": chunk_id,
        }
        self.text = text


class _FakeReranker:
    model = "fake-reranker"
    top_n = 1

    def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        del query, documents
        return [(1, 0.9), (0, 0.1)]


def _evidence() -> dict[str, Any]:
    return {
        "source": "manual",
        "page": 3,
        "snippet": "spark plug",
        "metadata": {"chunk_id": "chunk-1"},
    }


def _tool_call(tool_name: str) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "input": {},
        "output": {},
        "status": "success",
    }


def _find_span(trace, name: str):
    spans = _find_spans(trace, name)
    return spans[0] if spans else None


def _find_spans(trace, name: str) -> list[Any]:
    if trace is None:
        return []
    return [span for span in _walk_spans(trace.root_span) if span.name == name]


def _walk_spans(span):
    yield span
    for child in span.children:
        yield from _walk_spans(child)
