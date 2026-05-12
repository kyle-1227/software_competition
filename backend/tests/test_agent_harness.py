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
    assert [item.step.split(":", 1)[0] for item in response.plan] == [
        "plan",
        "retrieve",
        "evaluate",
        "answer",
    ]
    assert response.evidence
    assert response.tool_calls
    assert response.evaluation is not None
    assert response.trace_id is not None


@pytest.mark.anyio
async def test_agent_harness_executes_ai_coding_script() -> None:
    harness = AgentHarness()
    response = await harness.answer(
        QueryRequest(question="请生成一个 Python 脚本用于诊断", device_name="摩托车发动机")
    )

    assert response.ai_coding is not None
    assert response.ai_coding["language"] == "python"
    assert response.ai_coding["sandbox_result"]["allowed"] is True
    assert any(call.tool_name == "sandbox_execute" for call in response.tool_calls)
