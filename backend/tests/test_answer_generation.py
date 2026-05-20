from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.answer_generation import (
    draft_answer_with_llm,
)


@pytest.mark.anyio
async def test_draft_answer_fails_closed_without_evidence_id() -> None:
    result = await draft_answer_with_llm(
        SimpleNamespace(llm_client=None),
        {"question": "engine will not start", "evidence": []},
    )

    assert result["llm_model"] is None
    assert result["answer"] == ""
    assert result["llm_generation_failed"] is True
    assert result["safe_fallback_available"] is False
    assert any("insufficient evidence_id" in warning for warning in result["warnings"])


@pytest.mark.anyio
async def test_draft_answer_calls_llm_with_filtered_evidence() -> None:
    client = _RecordingLLMClient(text="Use evidence ev-1 only.")
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

    result = await draft_answer_with_llm(SimpleNamespace(llm_client=client), state)

    assert client.calls
    context = client.calls[0]["context"]
    assert context["evaluation_feedback"] == ""
    assert len(context["evidence"]) == 1
    assert context["evidence"][0]["evidence_id"] == "ev-1"
    assert "This must not be used" not in str(context)
    assert "reasoning" not in str(context)
    assert "thinking" not in str(context)
    assert "ev-1" in result["answer"]
    assert result["llm_generation_failed"] is False


@pytest.mark.anyio
async def test_draft_answer_marks_model_fallback_as_generation_failure() -> None:
    client = _RecordingLLMClient(
        text="ModelGateway deterministic fallback. Context: {}",
        warnings=["deterministic fallback used"],
    )

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

    assert client.calls
    assert result["answer"] == ""
    assert result["llm_generation_failed"] is True


def test_evaluator_optimizer_imports_answer_generation_not_graph_builder() -> None:
    source = Path("backend/app/services/evaluator_optimizer.py").read_text(
        encoding="utf-8"
    )

    assert "from app.services.answer_generation import draft_answer_with_llm" in source
    assert "app.services.graph.graph_builder" not in source
    assert "_draft_answer_with_llm" not in source


class _RecordingLLMClient:
    def __init__(
        self,
        text: str = "not allowed",
        warnings: list[str] | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.text = text
        self.warnings = warnings or []

    async def generate_text(self, prompt: str, context: dict | None = None, **kwargs):
        del kwargs
        self.calls.append({"prompt": prompt, "context": context})
        return SimpleNamespace(text=self.text, model="test-model", usage={}, warnings=self.warnings)
