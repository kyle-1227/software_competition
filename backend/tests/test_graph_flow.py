import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.schemas.query import QueryRequest, QueryResponse
from app.services.agent_harness_lc import AgentHarness
from app.services.graph import graph_builder
from app.services.graph.state import HarnessState
from app.verification.final_verifier import VERIFICATION_FAILED_ANSWER


def test_langgraph_uses_harness_state_schema(monkeypatch) -> None:
    captured = {}

    class FakeStateGraph:
        def __init__(self, state_schema):
            captured["state_schema"] = state_schema

        def add_node(self, *args, **kwargs):
            pass

        def set_entry_point(self, *args, **kwargs):
            pass

        def add_edge(self, *args, **kwargs):
            pass

        def add_conditional_edges(self, *args, **kwargs):
            pass

        def compile(self, *args, **kwargs):
            return self

    fake_langgraph = ModuleType("langgraph")
    fake_graph = ModuleType("langgraph.graph")
    fake_graph.END = "__end__"
    fake_graph.StateGraph = FakeStateGraph
    fake_langgraph.graph = fake_graph
    monkeypatch.setitem(sys.modules, "langgraph", fake_langgraph)
    monkeypatch.setitem(sys.modules, "langgraph.graph", fake_graph)
    monkeypatch.setattr(graph_builder, "_build_checkpointer", lambda: (None, None))

    graph_builder.build_harness_graph(SimpleNamespace(warnings=[]))

    assert captured["state_schema"] is HarnessState


@pytest.mark.anyio
async def test_graph_flow_returns_query_response() -> None:
    harness = AgentHarness()
    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert isinstance(response, QueryResponse)
    assert response.answer == VERIFICATION_FAILED_ANSWER
    assert response.plan
    assert response.evidence
    assert response.tool_calls
    assert response.evaluation is not None
    assert response.evaluation.confidence == 0.2
    assert response.trace_id is not None


@pytest.mark.anyio
async def test_graph_flow_handles_dict_response_conversion(monkeypatch) -> None:
    harness = AgentHarness()

    async def fake_ainvoke(state, config=None):
        del config
        return {
            "response": {
                "answer": "a",
                "plan": [],
                "evidence": [],
                "tool_calls": [],
                "evaluation": None,
                "trace_id": "t",
                "sop": [],
                "memory": [],
                "ai_coding": None,
                "llm_usage": None,
                "llm_model": None,
            }
        }

    monkeypatch.setattr(harness, "graph", type("G", (), {"ainvoke": fake_ainvoke})())
    response = await harness.answer(QueryRequest(question="x"))
    assert isinstance(response, QueryResponse)


@pytest.mark.anyio
async def test_graph_flow_blocks_deterministic_answer_without_evidence_id() -> None:
    harness = AgentHarness()

    response = await harness.answer(
        QueryRequest(question="火花塞间隙是多少？", device_name="摩托车发动机")
    )

    assert response.answer == VERIFICATION_FAILED_ANSWER
    assert response.evaluation is not None
    assert response.evaluation.is_safe is True
    assert response.evaluation.is_compliant is False
    assert response.evaluation.confidence == 0.2
