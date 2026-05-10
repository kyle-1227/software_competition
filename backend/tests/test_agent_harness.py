import pytest

from app.schemas.query import QueryRequest
from app.services.agent_harness_lc import AgentHarness


@pytest.mark.anyio
async def test_agent_harness_answer_contains_traceable_outputs() -> None:
    harness = AgentHarness()
    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert response.answer
    assert len(response.plan) >= 5
    assert response.evidence
    assert response.tool_calls
    assert response.evaluation is not None
    assert response.trace_id is not None
