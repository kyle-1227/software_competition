import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.schemas.query import QueryRequest, QueryResponse
from app.services.agent_harness_lc import AgentHarness
from app.services.graph import graph_builder
from app.services.graph.state import HarnessState


@dataclass
class FakeLLMResponse:
    text: str
    model: str | None = "deepseek-test"
    usage: dict[str, Any] | None = None
    warnings: list[str] | None = None


class RecordingLLMClient:
    def __init__(
        self,
        response: FakeLLMResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeLLMResponse(
            text="LLM 诊断建议：请先停机断电，并引用 P.3 的火花塞检查片段。"
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        self.calls.append({"prompt": prompt, "context": context})
        if self.error is not None:
            raise self.error
        return self.response


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
    assert response.answer
    assert response.plan
    assert response.evidence
    assert response.tool_calls
    assert response.evaluation is not None
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
async def test_draft_answer_node_calls_llm_and_returns_model_usage() -> None:
    llm_client = RecordingLLMClient(
        FakeLLMResponse(
            text="LLM 诊断建议：引用 P.3 检查火花塞间隙，先停机断电。",
            model="deepseek-v4-test",
            usage={"input_tokens": 10, "output_tokens": 8},
            warnings=[],
        )
    )
    harness = AgentHarness(llm_client=llm_client)

    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert llm_client.calls
    assert "设备检修智能辅助系统" in llm_client.calls[0]["prompt"]
    assert "LLM 诊断建议" in response.answer
    assert response.evaluation is not None

    context = llm_client.calls[0]["context"]
    assert {
        "question",
        "device_name",
        "device_model",
        "memory",
        "evidence",
        "tool_calls",
        "sandbox_result",
        "ai_coding",
        "evaluation",
        "warnings",
    }.issubset(context)


@pytest.mark.anyio
async def test_draft_answer_node_falls_back_when_llm_raises() -> None:
    llm_client = RecordingLLMClient(error=RuntimeError("provider unavailable"))
    harness = AgentHarness(llm_client=llm_client)

    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert llm_client.calls
    assert response.answer
    assert response.evaluation is not None
    assert "provider unavailable" not in response.answer


@pytest.mark.anyio
async def test_draft_answer_node_uses_local_answer_for_provider_fallback_text() -> None:
    llm_client = RecordingLLMClient(
        FakeLLMResponse(
            text="当前使用 deterministic fallback。上下文摘要：{}",
            model="deepseek-v4-test",
            warnings=["DeepSeek 未配置或不可用，已使用 fallback。"],
        )
    )
    harness = AgentHarness(llm_client=llm_client)

    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert "deterministic fallback" not in response.answer
    assert "上下文摘要" not in response.answer
    assert response.answer
    assert response.evaluation is not None


@pytest.mark.anyio
async def test_draft_answer_node_filters_reasoning_fields_from_context_and_answer() -> None:
    llm_client = RecordingLLMClient(
        FakeLLMResponse(
            text="诊断建议：chain_of_thought reasoning_content thinking 均不应外显。引用 P.3。",
            model="deepseek-v4-test",
            warnings=[],
        )
    )
    harness = AgentHarness(llm_client=llm_client)

    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    context_text = repr(llm_client.calls[0]["context"])
    assert "reasoning_content" not in context_text
    assert "thinking" not in context_text
    assert "chain_of_thought" not in context_text
    assert "reasoning_content" not in response.answer
    assert "thinking" not in response.answer
    assert "chain_of_thought" not in response.answer
