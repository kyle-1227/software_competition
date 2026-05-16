from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.core.config import settings
from app.schemas.orchestrator import OrchestratorDecision
from app.services.graph import graph_builder
from app.services.graph.state import HarnessState
from app.services.tool_registry import ToolResult


def test_build_harness_graph_always_uses_new_graph(monkeypatch) -> None:
    captured = _install_fake_langgraph(monkeypatch)
    monkeypatch.setattr(graph_builder, "_build_checkpointer", lambda: (None, None))

    graph_builder.build_harness_graph(SimpleNamespace(warnings=[]))

    assert not hasattr(settings, "use_" + "orchestrator")
    assert captured["state_schema"] is HarnessState
    assert "orchestrator_node" in captured["nodes"]
    assert "worker_executor_node" in captured["nodes"]


def test_old_graph_builder_symbols_removed() -> None:
    assert not hasattr(graph_builder, "_build_" + "legacy_graph")


def test_shared_nodes_exclude_old_only_nodes() -> None:
    nodes = graph_builder._build_shared_nodes(_services())
    old_only = {
        "plan_" + "node",
        "retrieval_" + "node",
        "ai_coding_" + "node",
        "sandbox_" + "node",
        "route_ai_" + "coding",
    }

    assert old_only.isdisjoint(nodes)
    assert {
        "intake_node",
        "memory_load_node",
        "draft_answer_node",
        "compliance_node",
        "evaluator_node",
        "trace_node",
        "memory_save_node",
        "finalize_node",
    }.issubset(nodes)


@pytest.mark.anyio
async def test_worker_executor_uses_manual_lookup_retry_helper(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_lookup(state, services, question_override=None):
        del services
        calls.append({"state": state, "question_override": question_override})
        return {
            "evidence": [_evidence()],
            "tool_calls": [{"tool_name": "manual_lookup", "input": {}, "status": "success"}],
            "warnings": ["fallback"],
            "degradation_events": [],
            "_manual_lookup_attempts": 1,
        }

    monkeypatch.setattr(graph_builder, "_run_manual_lookup_with_retry", fake_lookup)
    services = _services(worker_dispatcher=_EmptyDispatcher())
    nodes = graph_builder._build_new_nodes(services)
    update = await nodes["worker_executor_node"](
        {
            "question": "q",
            "_orchestrator_decision": OrchestratorDecision(
                intent="fault_triage",
                workers=["fault_triage"],
            ),
            "evidence": [],
            "tool_calls": [],
            "warnings": [],
        }
    )

    assert len(calls) == 1
    assert update["evidence"] == [_evidence()]
    assert update["warnings"] == ["fallback"]


@pytest.mark.anyio
async def test_retrieval_retry_uses_manual_lookup_retry_helper(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_lookup(state, services, question_override=None):
        del services
        calls.append({"state": state, "question_override": question_override})
        return {
            "evidence": [_evidence()],
            "tool_calls": [{"tool_name": "manual_lookup", "input": {}, "status": "success"}],
            "warnings": [],
            "degradation_events": [],
            "_manual_lookup_attempts": 1,
        }

    monkeypatch.setattr(graph_builder, "_run_manual_lookup_with_retry", fake_lookup)
    nodes = graph_builder._build_new_nodes(_services())
    update = await nodes["retrieval_retry_node"](
        {"question": "q", "evidence": [], "tool_calls": []}
    )

    assert len(calls) == 1
    assert calls[0]["question_override"]
    assert update["retrieval_retry_count"] == 1
    assert update["evidence"] == [_evidence()]


@pytest.mark.anyio
async def test_fallback_graph_uses_new_flow(monkeypatch) -> None:
    sequence: list[str] = []
    monkeypatch.setattr(graph_builder, "_build_new_nodes", lambda services: _fake_nodes(sequence))

    graph = graph_builder._build_fallback_graph(SimpleNamespace())
    result = await graph.ainvoke({"question": "q"})

    assert not hasattr(graph, "_ainvoke_" + "legacy_graph")
    assert "orchestrator_node" in sequence
    assert "worker_executor_node" in sequence
    assert result["response"]["answer"] == "ok"


class _RecordingStateGraph:
    def __init__(self, state_schema):
        self.state_schema = state_schema
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


def _install_fake_langgraph(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class CapturingStateGraph(_RecordingStateGraph):
        def __init__(self, state_schema):
            super().__init__(state_schema)
            captured["state_schema"] = state_schema
            captured["nodes"] = self.nodes

    fake_langgraph = ModuleType("langgraph")
    fake_graph = ModuleType("langgraph.graph")
    fake_graph.END = "__end__"
    fake_graph.StateGraph = CapturingStateGraph
    fake_langgraph.graph = fake_graph
    monkeypatch.setitem(sys.modules, "langgraph", fake_langgraph)
    monkeypatch.setitem(sys.modules, "langgraph.graph", fake_graph)
    return captured


class _EmptyDispatcher:
    async def dispatch(self, decision, state, services):
        del decision, state, services
        return [{"worker_outputs": [{"worker": "fault_triage"}]}]


class _ManualLookupRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=True, data=[_evidence()])


def _services(worker_dispatcher: Any | None = None) -> SimpleNamespace:
    from app.services.agent_loop.controller import AgentLoopController
    from app.services.agent_loop.policy import AgentLoopPolicy

    return SimpleNamespace(
        orchestrator=object(),
        input_guardrail=object(),
        worker_dispatcher=worker_dispatcher or object(),
        llm_client=None,
        llm_evaluator=object(),
        evaluator_optimizer=object(),
        output_guardrail=object(),
        tool_registry=_ManualLookupRegistry(),
        trace_store=object(),
        memory_store=object(),
        sandbox_executor=object(),
        evaluator=object(),
        warnings=[],
        agent_loop_policy=AgentLoopPolicy(),
        agent_loop_controller=AgentLoopController(),
    )


def _fake_nodes(sequence: list[str]) -> dict[str, Any]:
    async def node(name: str, update: dict[str, Any] | None = None):
        async def inner(state):
            del state
            sequence.append(name)
            return update or {}

        return inner

    async def intake(state):
        sequence.append("intake_node")
        return {
            "question": state.get("question", ""),
            "session_id": "s",
            "trace_id": "t",
            "errors": [],
            "warnings": [],
            "evidence": [_evidence()],
            "tool_calls": [],
            "answer": "",
        }

    async def finalize(state):
        sequence.append("finalize_node")
        response = {
            "answer": state.get("answer", "ok") or "ok",
            "plan": [],
            "evidence": state.get("evidence", []),
            "tool_calls": state.get("tool_calls", []),
            "evaluation": state.get("evaluation"),
            "trace_id": "t",
            "sop": [],
            "memory": [],
            "ai_coding": None,
            "llm_usage": None,
            "llm_model": None,
        }
        return {"response": response, **response}

    return {
        "intake_node": intake,
        "input_guardrail_node": awaitable_node(sequence, "input_guardrail_node", {"guardrail_passed": True}),
        "memory_load_node": awaitable_node(sequence, "memory_load_node", {"memory": []}),
        "orchestrator_node": awaitable_node(sequence, "orchestrator_node", {"plan": []}),
        "worker_executor_node": awaitable_node(sequence, "worker_executor_node", {}),
        "loop_decision_node": awaitable_node(
            sequence,
            "loop_decision_node",
            {"_agent_loop_decision": {"action": "FINALIZE"}},
        ),
        "retrieval_retry_node": awaitable_node(sequence, "retrieval_retry_node", {}),
        "evaluator_optimizer_node": awaitable_node(
            sequence,
            "evaluator_optimizer_node",
            {"answer": "ok", "evaluation": {"confidence": 1.0, "is_safe": True}},
        ),
        "post_eval_loop_decision_node": awaitable_node(
            sequence,
            "post_eval_loop_decision_node",
            {"_agent_loop_decision": {"action": "FINALIZE"}},
        ),
        "answer_regeneration_node": awaitable_node(sequence, "answer_regeneration_node", {}),
        "approval_node": awaitable_node(sequence, "approval_node", {"answer": "approval"}),
        "clarification_node": awaitable_node(sequence, "clarification_node", {"answer": "clarify"}),
        "fail_safe_node": awaitable_node(sequence, "fail_safe_node", {"answer": "fail"}),
        "output_guardrail_node": awaitable_node(sequence, "output_guardrail_node", {}),
        "draft_answer_node": awaitable_node(sequence, "draft_answer_node", {"answer": "ok"}),
        "compliance_node": awaitable_node(sequence, "compliance_node", {}),
        "evaluator_node": awaitable_node(
            sequence,
            "evaluator_node",
            {"evaluation": {"confidence": 1.0, "is_safe": True}},
        ),
        "trace_node": awaitable_node(sequence, "trace_node", {}),
        "memory_save_node": awaitable_node(sequence, "memory_save_node", {"memory": []}),
        "finalize_node": finalize,
        "route_after_guardrail": lambda state: "continue",
        "route_loop_decision": lambda state: "evaluate",
        "route_after_retrieval_retry": lambda state: "evaluate",
        "route_post_eval_loop_decision": lambda state: "output",
    }


def awaitable_node(
    sequence: list[str],
    name: str,
    update: dict[str, Any],
):
    async def inner(state):
        del state
        sequence.append(name)
        return update

    return inner


def _evidence() -> dict[str, Any]:
    return {
        "source": "manual",
        "page": 3,
        "snippet": "spark plug gap",
        "metadata": {"chunk_id": "chunk-1"},
    }
