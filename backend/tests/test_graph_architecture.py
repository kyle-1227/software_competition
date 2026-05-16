from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.graph.graph_builder import _build_new_graph


def test_graph_registers_new_harness_nodes() -> None:
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)

    assert {
        "orchestrator_node",
        "worker_executor_node",
        "loop_decision_node",
        "evaluator_optimizer_node",
        "trace_node",
        "finalize_node",
    }.issubset(graph.nodes)
    assert ("answer_regeneration_node", "evaluator_optimizer_node") in graph.edges


def test_graph_does_not_register_removed_legacy_nodes() -> None:
    graph = _build_new_graph(_services(), _RecordingStateGraph, "__end__", None)

    assert {
        "plan_node",
        "retrieval_node",
        "ai_coding_node",
        "sandbox_node",
        "route_ai_coding",
    }.isdisjoint(graph.nodes)


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
