from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.answer_generation import (
    INSUFFICIENT_EVIDENCE_ANSWER,
    LOCAL_DIAGNOSTIC_MODEL,
    draft_answer_with_llm,
)


@pytest.mark.anyio
async def test_draft_answer_returns_insufficient_evidence_without_evidence_id() -> None:
    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=None),
        {"question": "engine will not start", "evidence": []},
    )

    assert result["llm_model"] == LOCAL_DIAGNOSTIC_MODEL
    assert result["answer"] == INSUFFICIENT_EVIDENCE_ANSWER
    assert any("insufficient evidence_id" in warning for warning in result["warnings"])


@pytest.mark.anyio
async def test_draft_answer_summarizes_only_evidence_with_evidence_id() -> None:
    state = {
        "question": "engine will not start",
        "device_name": "engine",
        "evidence": [
            {
                "evidence_id": "ev-1",
                "source": "manual.pdf",
                "page": 8,
                "snippet": "Check the starter relay connector.",
                "metadata": {
                    "chunk_id": "c1",
                    "reasoning": "hidden",
                    "thinking": "hidden",
                },
            },
            {
                "source": "manual::placeholder",
                "snippet": "This must not be used.",
                "metadata": {"retriever": "llama-index-placeholder"},
            },
        ],
    }

    result = await draft_answer_with_llm(SimpleNamespace(llm_client=None), state)

    assert "ev-1" in result["answer"]
    assert "Check the starter relay connector." in result["answer"]
    assert "This must not be used" not in result["answer"]
    assert "reasoning" not in result["answer"]
    assert "thinking" not in result["answer"]


@pytest.mark.anyio
async def test_draft_answer_does_not_call_llm_even_when_available() -> None:
    client = _RecordingLLMClient()

    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=client),
        {
            "question": "q",
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "source": "manual",
                    "snippet": "Only this evidence may appear.",
                    "metadata": {},
                }
            ],
        },
    )

    assert client.calls == []
    assert "Only this evidence may appear." in result["answer"]


def test_evaluator_optimizer_imports_answer_generation_not_graph_builder() -> None:
    source = Path("backend/app/services/evaluator_optimizer.py").read_text(
        encoding="utf-8"
    )

    assert "from app.services.answer_generation import draft_answer_with_llm" in source
    assert "app.services.graph.graph_builder" not in source
    assert "_draft_answer_with_llm" not in source


class _RecordingLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_text(self, prompt: str, context: dict | None = None):
        self.calls.append({"prompt": prompt, "context": context})
        return SimpleNamespace(text="not allowed")
