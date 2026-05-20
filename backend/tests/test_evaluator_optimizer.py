from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.query import EvaluationResult
from app.services.evaluator_optimizer import EvaluatorOptimizer
from app.services.tool_registry import ToolResult


@pytest.mark.anyio
async def test_compliance_check_uses_tool_broker_not_registry() -> None:
    broker = _RecordingBroker()
    services = SimpleNamespace(
        llm_client=_LLM(),
        tool_broker=broker,
        tool_registry=_ExplodingRegistry(),
        trace_store=None,
    )

    result = await EvaluatorOptimizer(_Evaluator()).generate_and_evaluate(
        {
            "question": "q",
            "risk_level": "high",
            "evidence": [{"evidence_id": "ev-1", "source": "manual", "snippet": "safe"}],
            "tool_calls": [],
        },
        services,
    )

    assert broker.calls == [
        {
            "name": "compliance_check",
            "caller": "evaluator_optimizer",
            "risk_level": "high",
        }
    ]
    assert result["tool_calls"][-1]["brokered"] is True


@pytest.mark.anyio
async def test_evaluator_feedback_is_injected_into_regeneration() -> None:
    llm = _LLM()
    services = SimpleNamespace(
        llm_client=llm,
        tool_broker=_RecordingBroker(),
        trace_store=None,
    )

    await EvaluatorOptimizer(_TwoStepEvaluator()).generate_and_evaluate(
        {
            "question": "q",
            "evidence": [{"evidence_id": "ev-1", "source": "manual", "snippet": "safe"}],
            "tool_calls": [],
        },
        services,
    )

    assert len(llm.calls) >= 2
    assert llm.calls[1]["context"]["evaluation_feedback"] == "add safety detail"


class _LLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None, **kwargs):
        del kwargs
        self.calls.append({"prompt": prompt, "context": context or {}})
        return SimpleNamespace(
            text="Safe answer from ev-1.",
            model="test-model",
            usage={},
            warnings=[],
        )


class _RecordingBroker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        caller: str = "unknown",
        risk_level: str = "unknown",
        trace_id: str | None = None,
        run_id: str | None = None,
        approved: bool = False,
    ) -> ToolResult:
        del payload, trace_id, run_id, approved
        self.calls.append({"name": name, "caller": caller, "risk_level": risk_level})
        return ToolResult(
            tool_name=name,
            success=True,
            data={"is_safe": True},
            metadata={"brokered": True},
        )


class _ExplodingRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del name, payload
        raise AssertionError("ToolRegistry must not be used for compliance_check")


class _Evaluator:
    async def evaluate(self, answer, evidence, tool_calls, sop):
        del answer, evidence, tool_calls, sop
        return EvaluationResult(is_safe=True, is_compliant=True, confidence=0.95)


class _TwoStepEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, answer, evidence, tool_calls, sop):
        del answer, evidence, tool_calls, sop
        self.calls += 1
        if self.calls == 1:
            return EvaluationResult(
                is_safe=True,
                is_compliant=False,
                confidence=0.4,
                issues=["missing safety detail"],
                feedback="add safety detail",
            )
        return EvaluationResult(is_safe=True, is_compliant=True, confidence=0.95)
