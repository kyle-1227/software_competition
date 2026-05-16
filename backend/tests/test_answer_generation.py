from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.answer_generation import (
    LOCAL_DIAGNOSTIC_MODEL,
    draft_answer_with_llm,
)


@dataclass
class FakeLLMResponse:
    text: str
    model: str | None = "fake-answer-model"
    usage: dict[str, Any] | None = None
    warnings: list[str] | None = None


class RecordingLLMClient:
    def __init__(self, response: FakeLLMResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        self.calls.append({"prompt": prompt, "context": context})
        return self.response


@pytest.mark.anyio
async def test_draft_answer_uses_local_fallback_without_llm() -> None:
    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=None),
        {"question": "engine will not start", "evidence": []},
    )

    assert result["llm_model"] == LOCAL_DIAGNOSTIC_MODEL
    assert result["answer"]
    assert any("LLM client unavailable" in warning for warning in result["warnings"])


@pytest.mark.anyio
async def test_draft_answer_filters_reasoning_fields() -> None:
    client = RecordingLLMClient(
        FakeLLMResponse(
            text="safe answer chain_of_thought reasoning_content thinking",
            usage={"input_tokens": 1},
        )
    )
    state = {
        "question": "engine will not start",
        "evidence": [
            {
                "snippet": "manual snippet",
                "metadata": {
                    "chunk_id": "c1",
                    "reasoning": "hidden",
                    "thinking": "hidden",
                },
            }
        ],
        "tool_calls": [
            {
                "tool_name": "manual_lookup",
                "input": {"chain_of_thought": "hidden", "question": "q"},
            }
        ],
        "evaluation": {"reasoning_content": "hidden", "confidence": 0.8},
    }

    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=client),
        state,
    )

    context_text = repr(client.calls[0]["context"])
    assert "reasoning" not in context_text
    assert "thinking" not in context_text
    assert "chain_of_thought" not in context_text
    assert "reasoning_content" not in context_text
    assert "chain_of_thought" not in result["answer"]
    assert "reasoning_content" not in result["answer"]
    assert "thinking" not in result["answer"]


@pytest.mark.anyio
async def test_draft_answer_uses_llm_when_available() -> None:
    client = RecordingLLMClient(
        FakeLLMResponse(
            text="LLM answer",
            model="fake-model-v1",
            usage={"input_tokens": 10, "output_tokens": 4},
            warnings=[],
        )
    )

    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=client),
        {"question": "q", "evidence": []},
    )

    assert result["answer"] == "LLM answer"
    assert result["llm_model"] == "fake-model-v1"
    assert result["llm_usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert client.calls


@pytest.mark.anyio
async def test_draft_answer_fallback_on_empty_llm_response() -> None:
    client = RecordingLLMClient(FakeLLMResponse(text="", model="fake-model-v1"))

    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=client),
        {"question": "q", "evidence": []},
    )

    assert result["llm_model"] == LOCAL_DIAGNOSTIC_MODEL
    assert result["answer"]
    assert any("LLM returned empty answer" in warning for warning in result["warnings"])


def test_evaluator_optimizer_imports_answer_generation_not_graph_builder() -> None:
    source = Path("backend/app/services/evaluator_optimizer.py").read_text(
        encoding="utf-8"
    )

    assert "from app.services.answer_generation import draft_answer_with_llm" in source
    assert "app.services.graph.graph_builder" not in source
    assert "_draft_answer_with_llm" not in source
