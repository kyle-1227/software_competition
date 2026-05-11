import pytest

from app.schemas.query import QueryRequest, QueryResponse
from app.services.agent_harness_lc import AgentHarness


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
