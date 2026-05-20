from __future__ import annotations

from app.services.agent_loop.actions import AgentLoopAction
from app.services.agent_loop.controller import AgentLoopController
from app.services.agent_loop.policy import AgentLoopPolicy


def test_loop_decides_retry_retrieval_when_evidence_empty() -> None:
    decision = AgentLoopController().decide(
        {"question": "火花塞间隙是多少？", "evidence": [], "retrieval_retry_count": 0},
        _policy(),
    )

    assert decision.action == AgentLoopAction.RETRY_RETRIEVAL


def test_loop_decides_regenerate_when_confidence_low() -> None:
    decision = AgentLoopController().decide(
        {
            "question": "火花塞间隙是多少？",
            "evidence": [_evidence()],
            "evaluation": {"confidence": 0.3, "is_safe": True},
            "answer_regeneration_count": 0,
        },
        _policy(),
    )

    assert decision.action == AgentLoopAction.REGENERATE_ANSWER


def test_loop_requires_approval_for_high_risk_low_evidence() -> None:
    decision = AgentLoopController().decide(
        {
            "question": "直接更换 ECU 并执行脚本",
            "evidence": [],
            "retrieval_retry_count": 0,
        },
        _policy(),
    )

    assert decision.action == AgentLoopAction.REQUIRE_APPROVAL
    assert decision.requires_approval is True


def test_loop_fails_safe_after_max_steps() -> None:
    policy = _policy(max_loop_steps=3)
    decision = AgentLoopController().decide(
        {"question": "q", "loop_decision_count": 3},
        policy,
    )

    assert decision.action == AgentLoopAction.FAIL_SAFE


def test_loop_fails_safe_when_llm_generation_failed_without_safe_fallback() -> None:
    decision = AgentLoopController().decide(
        {
            "question": "q",
            "evidence": [_evidence()],
            "llm_generation_failed": True,
            "safe_fallback_available": False,
        },
        _policy(),
    )

    assert decision.action == AgentLoopAction.FAIL_SAFE


def test_loop_finalizes_when_evidence_and_evaluation_ok() -> None:
    decision = AgentLoopController().decide(
        {
            "question": "火花塞间隙是多少？",
            "evidence": [_evidence()],
            "evaluation": {"confidence": 0.9, "is_safe": True},
        },
        _policy(),
    )

    assert decision.action == AgentLoopAction.FINALIZE


def test_manual_lookup_degraded_placeholder_triggers_clarification_or_fail_safe() -> None:
    policy = _policy(max_retrieval_retries=2)
    decision = AgentLoopController().decide(
        {
            "question": "火花塞标准值是多少？",
            "evidence": [
                {
                    "source": "manual::degraded",
                    "snippet": "no manual evidence",
                    "metadata": {"retriever": "manual_lookup-degraded"},
                }
            ],
            "retrieval_retry_count": 2,
        },
        policy,
    )

    assert decision.action in {AgentLoopAction.ASK_CLARIFICATION, AgentLoopAction.FAIL_SAFE}


def _policy(**overrides) -> AgentLoopPolicy:
    values = {
        "max_loop_steps": 8,
        "max_tool_retries": 5,
        "max_retrieval_retries": 2,
        "max_answer_regenerations": 2,
        "evaluator_confidence_threshold": 0.7,
    }
    values.update(overrides)
    return AgentLoopPolicy(**values)


def _evidence() -> dict:
    return {
        "source": "manual",
        "page": 3,
        "snippet": "火花塞间隙 0.7-0.9 mm",
        "metadata": {"chunk_id": "chunk-1"},
    }
