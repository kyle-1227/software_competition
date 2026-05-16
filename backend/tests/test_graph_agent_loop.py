from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.schemas.query import SandboxResult
from app.services.agent_loop.policy import AgentLoopPolicy
from app.services.graph.graph_builder import (
    _build_new_graph,
    _build_new_nodes,
    _dedupe_tool_calls,
)
from app.services.tool_registry import ToolResult


def test_retry_tool_not_present_in_graph_conditional_edges(monkeypatch) -> None:
    monkeypatch.setattr(settings, "use_evaluator_optimizer", True)
    monkeypatch.setattr(settings, "use_output_guardrail", False)
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)

    all_route_names = {
        route_name
        for _, _, mapping in graph.conditional_edges
        for route_name in mapping
    }

    assert "retry_tool" not in all_route_names
    assert "RETRY_TOOL" not in all_route_names


@pytest.mark.anyio
async def test_graph_retries_retrieval_before_finalize() -> None:
    services = _services(tool_registry=_ManualLookupRegistry([_evidence()]))
    nodes = _build_new_nodes(services)
    state = {"question": "火花塞间隙？", "evidence": [], "tool_calls": []}

    decision_update = await nodes["loop_decision_node"](state)
    assert nodes["route_loop_decision"]({**state, **decision_update}) == "retry_retrieval"


@pytest.mark.anyio
async def test_retrieval_retry_success_enters_evaluator_optimizer() -> None:
    services = _services(tool_registry=_ManualLookupRegistry([_evidence()]))
    nodes = _build_new_nodes(services)
    state = {"question": "火花塞间隙？", "evidence": [], "tool_calls": []}

    update = await nodes["retrieval_retry_node"](state)
    route = nodes["route_after_retrieval_retry"]({**state, **update})

    assert route == "evaluate"
    assert update["retrieval_retry_count"] == 1
    assert update["evidence"][0]["metadata"]["chunk_id"] == "chunk-1"


@pytest.mark.anyio
async def test_retrieval_retry_limit_does_not_continue_loop() -> None:
    policy = AgentLoopPolicy(max_retrieval_retries=1)
    services = _services(tool_registry=_ManualLookupRegistry([]), policy=policy)
    nodes = _build_new_nodes(services)
    state = {
        "question": "火花塞标准值是多少？",
        "evidence": [_placeholder()],
        "retrieval_retry_count": 1,
        "tool_calls": [],
    }

    update = await nodes["loop_decision_node"](state)
    route = nodes["route_loop_decision"]({**state, **update})

    assert route in {"clarification", "fail_safe"}
    assert route != "retry_retrieval"


@pytest.mark.anyio
async def test_manual_lookup_degraded_placeholder_triggers_clarification_or_fail_safe() -> None:
    policy = AgentLoopPolicy(max_retrieval_retries=1)
    services = _services(tool_registry=_AlwaysFailRegistry(), policy=policy)
    nodes = _build_new_nodes(services)
    state = {"question": "火花塞标准值是多少？", "evidence": [], "tool_calls": []}

    retry_update = await nodes["retrieval_retry_node"](state)
    assert nodes["route_after_retrieval_retry"]({**state, **retry_update}) == "decide"

    decision_update = await nodes["loop_decision_node"]({**state, **retry_update})
    route = nodes["route_loop_decision"]({**state, **retry_update, **decision_update})

    assert route in {"clarification", "fail_safe"}


def test_blocked_guardrail_skips_loop() -> None:
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)
    guardrail_mapping = next(
        mapping
        for source, _, mapping in graph.conditional_edges
        if source == "input_guardrail_node"
    )

    assert guardrail_mapping["blocked"] == "finalize_node"
    assert graph.edges.count(("worker_executor_node", "loop_decision_node")) == 1


def test_dedupe_tool_calls_preserves_retry_attempts() -> None:
    calls = [
        {
            "tool_name": "manual_lookup",
            "input": {"question": "q"},
            "status": "failed",
            "attempt": attempt,
        }
        for attempt in range(1, 6)
    ]

    deduped = _dedupe_tool_calls(calls)

    assert len(deduped) == 5
    assert [call["attempt"] for call in deduped] == [1, 2, 3, 4, 5]


def test_dedupe_tool_calls_still_deduplicates_legacy_calls() -> None:
    call = {
        "tool_name": "manual_lookup",
        "input": {"question": "q"},
        "status": "success",
    }

    deduped = _dedupe_tool_calls([call, dict(call)])

    assert deduped == [call]


@pytest.mark.anyio
async def test_loop_decision_count_tracks_decisions_not_actions() -> None:
    services = _services()
    nodes = _build_new_nodes(services)
    state = {
        "question": "火花塞间隙？",
        "evidence": [_evidence()],
        "tool_calls": [],
    }

    first = await nodes["loop_decision_node"](state)
    second = await nodes["loop_decision_node"]({**state, **first})

    assert first["loop_decision_count"] == 1
    assert second["loop_decision_count"] == 2
    assert [item["decision_count"] for item in second["loop_history"]] == [1, 2]
    assert all("step" not in item for item in second["loop_history"])


class _RecordingStateGraph:
    def __init__(self, state_type: Any) -> None:
        self.state_type = state_type
        self.nodes: dict[str, Any] = {}
        self.edges: list[tuple[str, str]] = []
        self.conditional_edges: list[tuple[str, Any, dict[str, str]]] = []
        self.entry_point: str | None = None

    def add_node(self, name: str, node: Any) -> None:
        self.nodes[name] = node

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name

    def add_edge(self, source: str, target: str) -> None:
        self.edges.append((source, target))

    def add_conditional_edges(
        self,
        source: str,
        route_fn: Any,
        mapping: dict[str, str],
    ) -> None:
        self.conditional_edges.append((source, route_fn, mapping))

    def compile(self, checkpointer: Any = None) -> "_RecordingStateGraph":
        del checkpointer
        return self


class _ManualLookupRegistry:
    def __init__(self, evidence: list[dict[str, Any]]) -> None:
        self.evidence = evidence

    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=True, data=self.evidence)


class _AlwaysFailRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=False, error="boom")


class _NoopSandbox:
    def execute(self, script: str, language: str) -> SandboxResult:
        del script
        return SandboxResult(language=language, allowed=True, return_code=0)


class _NoopEvaluatorOptimizer:
    async def generate_and_evaluate(
        self,
        state: dict[str, Any],
        services: Any,
    ) -> dict[str, Any]:
        del services
        return {
            "answer": state.get("answer", "ok") or "ok",
            "evaluation": {"confidence": 0.95, "is_safe": True, "is_compliant": True},
        }


def _services(
    *,
    tool_registry: Any | None = None,
    policy: AgentLoopPolicy | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        orchestrator=object(),
        input_guardrail=object(),
        worker_dispatcher=object(),
        llm_client=None,
        llm_evaluator=object(),
        evaluator_optimizer=_NoopEvaluatorOptimizer(),
        output_guardrail=object(),
        tool_registry=tool_registry or _ManualLookupRegistry([]),
        trace_store=object(),
        memory_store=object(),
        sandbox_executor=_NoopSandbox(),
        evaluator=object(),
        warnings=[],
        agent_loop_policy=policy or AgentLoopPolicy(),
        agent_loop_controller=None,
    )


def _evidence() -> dict[str, Any]:
    return {
        "source": "manual",
        "page": 3,
        "snippet": "火花塞间隙 0.7-0.9 mm",
        "metadata": {"chunk_id": "chunk-1"},
    }


def _placeholder() -> dict[str, Any]:
    return {
        "source": "manual::degraded",
        "page": None,
        "snippet": "no manual evidence",
        "metadata": {"retriever": "manual_lookup-degraded"},
    }
