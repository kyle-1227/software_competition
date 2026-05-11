import pytest

from app.schemas.query import QueryRequest
from app.services.agent_harness_lc import AgentHarness


@pytest.mark.anyio
async def test_memory_is_session_scoped() -> None:
    harness = AgentHarness()
    first = await harness.answer(
        QueryRequest(
            question="发动机无法启动怎么办",
            device_name="摩托车发动机",
            session_id="session-a",
        )
    )
    second = await harness.answer(
        QueryRequest(
            question="再查一次同一台设备",
            device_name="摩托车发动机",
            session_id="session-a",
        )
    )

    assert first.trace_id is not None
    assert second.memory is not None


@pytest.mark.anyio
async def test_memory_different_sessions_do_not_pollute() -> None:
    harness = AgentHarness()
    one = await harness.answer(
        QueryRequest(question="q1", device_name="摩托车发动机", session_id="a")
    )
    two = await harness.answer(
        QueryRequest(question="q2", device_name="摩托车发动机", session_id="b")
    )

    assert one.trace_id is not None
    assert two.trace_id is not None
