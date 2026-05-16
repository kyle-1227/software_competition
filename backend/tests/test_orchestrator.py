from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.orchestrator import Orchestrator


@pytest.mark.anyio
async def test_orchestrator_keyword_fallback_without_removed_flag() -> None:
    assert not hasattr(settings, "use_" + "orchestrator")

    decision = await Orchestrator(llm_client=None).classify_and_plan("python script")

    assert "ai_coding" in decision.workers


@pytest.mark.anyio
async def test_orchestrator_uses_llm_when_available() -> None:
    llm = _FakeLLM(
        text='{"intent":"sop_guidance","workers":["sop_guidance"],'
        '"reasoning":"ok","priority":"safety_first"}'
    )

    decision = await Orchestrator(llm_client=llm).classify_and_plan("maintenance SOP")

    assert llm.calls == 1
    assert decision.intent == "sop_guidance"
    assert decision.workers == ["sop_guidance"]


@pytest.mark.anyio
async def test_orchestrator_falls_back_when_llm_fails() -> None:
    llm = _FakeLLM(error=RuntimeError("provider unavailable"))

    decision = await Orchestrator(llm_client=llm).classify_and_plan("python script")

    assert llm.calls == 1
    assert "ai_coding" in decision.workers


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.warnings: list[str] = []


class _FakeLLM:
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def generate_json(self, prompt, context):
        del prompt, context
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.text)
