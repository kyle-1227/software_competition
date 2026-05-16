from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.services.agent_loop.policy import AgentLoopPolicy
from app.services.graph import graph_builder
from app.services.graph.graph_builder import (
    _build_fallback_graph,
    _build_new_graph,
    _build_shared_nodes,
)


def test_use_evaluator_optimizer_setting_removed() -> None:
    assert not hasattr(settings, "use_evaluator_optimizer")


def test_new_graph_always_registers_evaluator_optimizer() -> None:
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)

    assert {
        "evaluator_optimizer_node",
        "post_eval_loop_decision_node",
        "answer_regeneration_node",
    }.issubset(graph.nodes)
    assert {
        "draft_answer_node",
        "compliance_node",
        "evaluator_node",
    }.isdisjoint(graph.nodes)


def test_shared_nodes_exclude_draft_compliance_evaluator() -> None:
    nodes = _build_shared_nodes(_services())

    assert {
        "intake_node",
        "memory_load_node",
        "trace_node",
        "memory_save_node",
        "finalize_node",
    }.issubset(nodes)
    assert {
        "draft_answer_node",
        "compliance_node",
        "evaluator_node",
    }.isdisjoint(nodes)


@pytest.mark.anyio
async def test_fallback_graph_uses_evaluator_optimizer_only(monkeypatch) -> None:
    calls: list[str] = []

    async def node(name: str, update: dict[str, Any] | None = None):
        async def _inner(state: dict[str, Any]) -> dict[str, Any]:
            del state
            calls.append(name)
            return update or {}

        return _inner

    async def evaluator_optimizer_node_impl(state: dict[str, Any]) -> dict[str, Any]:
        del state
        calls.append("evaluator_optimizer_node")
        return {
            "answer": "ok",
            "evaluation": {
                "is_safe": True,
                "is_compliant": True,
                "confidence": 0.9,
                "issues": [],
            },
        }

    fake_nodes = {
        "intake_node": await node("intake_node"),
        "input_guardrail_node": await node("input_guardrail_node", {"guardrail_passed": True}),
        "route_after_guardrail": lambda state: "continue",
        "memory_load_node": await node("memory_load_node"),
        "orchestrator_node": await node("orchestrator_node"),
        "worker_executor_node": await node("worker_executor_node"),
        "loop_decision_node": await node("loop_decision_node"),
        "route_loop_decision": lambda state: "evaluate",
        "retrieval_retry_node": await node("retrieval_retry_node"),
        "route_after_retrieval_retry": lambda state: "evaluate",
        "evaluator_optimizer_node": evaluator_optimizer_node_impl,
        "post_eval_loop_decision_node": await node("post_eval_loop_decision_node"),
        "route_post_eval_loop_decision": lambda state: "output",
        "answer_regeneration_node": await node("answer_regeneration_node"),
        "approval_node": await node("approval_node"),
        "clarification_node": await node("clarification_node"),
        "fail_safe_node": await node("fail_safe_node"),
        "output_guardrail_node": await node("output_guardrail_node"),
        "trace_node": await node("trace_node"),
        "memory_save_node": await node("memory_save_node"),
        "finalize_node": await node("finalize_node"),
    }
    monkeypatch.setattr(graph_builder, "_build_new_nodes", lambda services: fake_nodes)
    monkeypatch.setattr(settings, "use_output_guardrail", False)

    graph = _build_fallback_graph(
        SimpleNamespace(agent_loop_policy=AgentLoopPolicy(max_answer_regenerations=1))
    )
    await graph.ainvoke({"question": "q"})

    assert "evaluator_optimizer_node" in calls
    assert "draft_answer_node" not in calls
    assert "compliance_node" not in calls
    assert "evaluator_node" not in calls


def test_answer_regeneration_routes_back_to_evaluator_optimizer() -> None:
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)
    post_eval_mapping = next(
        mapping
        for source, _, mapping in graph.conditional_edges
        if source == "post_eval_loop_decision_node"
    )

    assert post_eval_mapping["regenerate"] == "answer_regeneration_node"
    assert ("answer_regeneration_node", "evaluator_optimizer_node") in graph.edges


class _RecordingStateGraph:
    def __init__(self, state_type: Any) -> None:
        self.state_type = state_type
        self.nodes: dict[str, Any] = {}
        self.edges: list[tuple[str, str]] = []
        self.conditional_edges: list[tuple[str, Any, dict[str, str]]] = []

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


def _services() -> SimpleNamespace:
    return SimpleNamespace(
        orchestrator=object(),
        input_guardrail=object(),
        worker_dispatcher=object(),
        llm_client=None,
        llm_evaluator=object(),
        evaluator_optimizer=object(),
        output_guardrail=object(),
        tool_registry=object(),
        trace_store=object(),
        memory_store=object(),
        sandbox_executor=object(),
        evaluator=object(),
        warnings=[],
        agent_loop_policy=None,
        agent_loop_controller=None,
    )
