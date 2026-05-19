import json
from typing import Any

import pytest

from app.schemas.query import QueryRequest, QueryResponse
from app.services.agent_harness_lc import AgentHarness
from app.services.runtime import RuntimeResultAdapter, RuntimeStateFactory


class CapturingGraph:
    def __init__(self) -> None:
        self.initial_state: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(self, state: dict[str, Any], config=None):
        self.initial_state = state
        self.config = config
        return {
            **state,
            "answer": "ok",
            "plan": [],
            "evidence": [],
            "tool_calls": [],
            "evaluation": None,
            "trace_id": "trace-1",
            "sop": [],
            "memory": [],
            "ai_coding": None,
            "llm_usage": None,
            "llm_model": None,
        }


def test_runtime_state_factory_builds_harness_state_and_redacts_metadata() -> None:
    state = RuntimeStateFactory().from_query_request(
        QueryRequest(question="q", device_name="motor"),
        request_id="req-1",
        metadata={"api_key": "real-api-key", "http_path": "/api/query"},
    )

    assert state.request.request_id == "req-1"
    assert state.request.session_id == "motor"
    assert state.harness_state["question"] == "q"
    assert state.harness_state["runtime_contract"]["request_id"] == "req-1"

    rendered = json.dumps(state.model_dump(mode="json"), ensure_ascii=False)
    assert "real-api-key" not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.anyio
async def test_agent_harness_runs_query_through_runtime_contract() -> None:
    graph = CapturingGraph()
    harness = AgentHarness(graph=graph)

    response = await harness.answer(
        QueryRequest(question="q", session_id="session-1"),
        request_id="req-1",
    )

    assert isinstance(response, QueryResponse)
    assert response.answer == "ok"
    assert graph.initial_state is not None
    assert graph.initial_state["runtime_request"]["request_id"] == "req-1"
    assert graph.initial_state["runtime_contract"]["status"] == "running"
    assert graph.config == {"configurable": {"thread_id": "session-1"}}


def test_runtime_result_adapter_redacts_internal_errors() -> None:
    result = RuntimeResultAdapter().from_harness_state(
        {
            "answer": "ok",
            "plan": [],
            "evidence": [],
            "tool_calls": [],
            "evaluation": None,
            "trace_id": "trace-1",
            "sop": [],
            "memory": [],
            "ai_coding": None,
            "llm_usage": None,
            "llm_model": None,
            "errors": ["api_key=real-api-key password=real-password"],
        },
        request_id="req-1",
    )

    rendered = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "real-api-key" not in rendered
    assert "real-password" not in rendered
    assert result.errors == ["api_key=[REDACTED] password=[REDACTED]"]
