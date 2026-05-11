import pytest

from app.schemas.query import QueryRequest
from app.services.agent_harness_lc import AgentHarness


@pytest.mark.anyio
async def test_ai_coding_branch_is_used_for_code_questions() -> None:
    harness = AgentHarness()
    response = await harness.answer(
        QueryRequest(question="请生成一个 Python 脚本用于诊断", device_name="摩托车发动机")
    )

    assert response.ai_coding is not None
    assert response.ai_coding["language"] == "python"
    assert response.ai_coding["sandbox_result"]["allowed"] is True


@pytest.mark.anyio
async def test_regular_question_still_returns_answer() -> None:
    harness = AgentHarness()
    response = await harness.answer(
        QueryRequest(question="发动机无法启动怎么办", device_name="摩托车发动机")
    )

    assert response.answer
    assert response.ai_coding is None or response.ai_coding["language"] in {"python", "sql"}
