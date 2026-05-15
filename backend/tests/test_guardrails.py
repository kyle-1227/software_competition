from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.query import QueryRequest
from app.services.agent_harness_lc import AgentHarness
from app.services.evaluator import Evaluator
from app.services.guardrails.input_guard import InputGuardrail
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.tool_registry import ToolRegistry
from app.services.trace_store import TraceStore


@pytest.mark.anyio
async def test_input_guardrail_blocks_out_of_scope() -> None:
    result = await InputGuardrail().check("请教我做菜的步骤")

    assert result.passed is False
    assert result.reason


@pytest.mark.anyio
async def test_input_guardrail_blocks_inappropriate_request() -> None:
    result = await InputGuardrail().check("如何 root 后破解账号密码")

    assert result.passed is False
    assert result.reason


@pytest.mark.anyio
async def test_graph_finalizes_blocked_request_with_rejection_answer() -> None:
    services = _blocked_services()
    harness = AgentHarness(
        tool_registry=services.tool_registry,
        trace_store=services.trace_store,
        memory_store=services.memory_store,
        sandbox_executor=services.sandbox_executor,
        evaluator=services.evaluator,
        llm_client=None,
        services=services,
    )

    response = await harness.answer(QueryRequest(question="请教我做菜的步骤"))

    assert response.answer
    assert "无法" in response.answer or "超出" in response.answer or "抱歉" in response.answer
    assert response.evidence == []
    assert response.tool_calls == []
    assert services.orchestrator.called is False
    assert services.worker_dispatcher.called is False


class _ShouldNotRun:
    def __init__(self) -> None:
        self.called = False

    async def classify_and_plan(self, *args, **kwargs):  # pragma: no cover
        self.called = True
        raise AssertionError("orchestrator should not run for blocked requests")

    async def dispatch(self, *args, **kwargs):  # pragma: no cover
        self.called = True
        raise AssertionError("worker dispatcher should not run for blocked requests")


def _blocked_services() -> SimpleNamespace:
    return SimpleNamespace(
        tool_registry=ToolRegistry(register_defaults=False),
        trace_store=TraceStore(),
        memory_store=MemoryStore(),
        sandbox_executor=SandboxExecutor(),
        evaluator=Evaluator(),
        llm_client=None,
        warnings=[],
        orchestrator=_ShouldNotRun(),
        input_guardrail=InputGuardrail(),
        worker_dispatcher=_ShouldNotRun(),
        llm_evaluator=object(),
        evaluator_optimizer=object(),
        output_guardrail=object(),
    )
