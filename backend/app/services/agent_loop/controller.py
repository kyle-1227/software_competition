from __future__ import annotations

from typing import Any

from app.services.agent_loop.actions import AgentLoopAction, AgentLoopDecision
from app.services.agent_loop.policy import AgentLoopPolicy

_HIGH_RISK_TERMS = (
    "拆卸",
    "更换",
    "带电",
    "刷写",
    "ECU",
    "执行脚本",
    "直接换",
)
_CONCLUSION_TERMS = ("确定", "直接", "必须", "参数", "标准值", "多少", "结论")
_PLACEHOLDER_RETRIEVERS = {
    "llama-index-placeholder",
    "manual_lookup-degraded",
}


class AgentLoopController:
    def decide(
        self,
        state: dict[str, Any],
        policy: AgentLoopPolicy | None = None,
    ) -> AgentLoopDecision:
        policy = policy or AgentLoopPolicy.from_settings()
        if not policy.enabled:
            return AgentLoopDecision(
                action=AgentLoopAction.FINALIZE,
                reason="Agent Loop disabled",
            )

        if state.get("guardrail_passed") is False:
            return AgentLoopDecision(
                action=AgentLoopAction.FINALIZE,
                reason="Input guardrail blocked the request",
            )

        if _loop_decision_count(state) >= policy.max_loop_steps:
            return AgentLoopDecision(
                action=AgentLoopAction.FAIL_SAFE,
                reason="达到最大 Agent Loop 步数",
                confidence=0.0,
            )

        high_risk = _is_high_risk(state)
        valid_evidence = has_effective_evidence(state)
        placeholder_used = placeholder_used_in_state(state)

        if (
            high_risk
            and policy.high_risk_requires_approval
            and (not valid_evidence or _sandbox_unsafe(state) or _evaluation_unsafe(state))
        ):
            return AgentLoopDecision(
                action=AgentLoopAction.REQUIRE_APPROVAL,
                reason="高风险或证据不足，需要人工确认",
                requires_approval=True,
                confidence=0.4,
            )

        if (
            policy.evidence_required_for_answer
            and (not valid_evidence or placeholder_used)
        ):
            if int(state.get("retrieval_retry_count", 0) or 0) < policy.max_retrieval_retries:
                return AgentLoopDecision(
                    action=AgentLoopAction.RETRY_RETRIEVAL,
                    reason="手册证据为空或仍为 placeholder，尝试重新检索",
                    confidence=0.6,
                    target="manual_lookup",
                    metadata={"placeholder_used": placeholder_used},
                )
            action = (
                AgentLoopAction.ASK_CLARIFICATION
                if _needs_clarification(state)
                else AgentLoopAction.FAIL_SAFE
            )
            return AgentLoopDecision(
                action=action,
                reason="检索重试已达到上限，仍无有效手册证据",
                confidence=0.2,
                metadata={"placeholder_used": placeholder_used},
            )

        evaluation = state.get("evaluation") if isinstance(state.get("evaluation"), dict) else {}
        confidence = float(evaluation.get("confidence", 1.0) or 0.0)
        if (
            confidence < policy.evaluator_confidence_threshold
            and int(state.get("answer_regeneration_count", 0) or 0)
            < policy.max_answer_regenerations
        ):
            return AgentLoopDecision(
                action=AgentLoopAction.REGENERATE_ANSWER,
                reason="Evaluator confidence below threshold",
                confidence=confidence,
            )

        if policy.high_risk_requires_approval and (
            high_risk or _sandbox_unsafe(state)
        ):
            return AgentLoopDecision(
                action=AgentLoopAction.REQUIRE_APPROVAL,
                reason="高风险操作或安全评估未通过，需要人工确认",
                requires_approval=True,
                confidence=0.5,
            )

        return AgentLoopDecision(
            action=AgentLoopAction.FINALIZE,
            reason="Evidence and evaluation are sufficient",
        )


def has_effective_evidence(state: dict[str, Any]) -> bool:
    evidence = state.get("evidence", [])
    if not isinstance(evidence, list):
        return False
    return any(is_effective_evidence(item) for item in evidence)


def _loop_decision_count(state: dict[str, Any]) -> int:
    # Compatibility: older in-flight state used loop_step for the same concept.
    value = state.get("loop_decision_count", state.get("loop_step", 0))
    return int(value or 0)


def is_effective_evidence(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not item.get("snippet"):
        return False
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    retriever = str(metadata.get("retriever", ""))
    return retriever not in _PLACEHOLDER_RETRIEVERS


def placeholder_used_in_state(state: dict[str, Any]) -> bool:
    evidence = state.get("evidence", [])
    if not isinstance(evidence, list):
        return False
    return any(_is_placeholder(item) for item in evidence)


def _is_placeholder(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    retriever = str(metadata.get("retriever", ""))
    return retriever in _PLACEHOLDER_RETRIEVERS or "placeholder" in retriever


def _is_high_risk(state: dict[str, Any]) -> bool:
    text = str(state.get("question", ""))
    return any(term in text for term in _HIGH_RISK_TERMS)


def _needs_clarification(state: dict[str, Any]) -> bool:
    question = str(state.get("question", ""))
    if not question.strip():
        return True
    return any(term in question for term in _CONCLUSION_TERMS) or len(question) < 20


def _sandbox_unsafe(state: dict[str, Any]) -> bool:
    sandbox = state.get("sandbox_result")
    return isinstance(sandbox, dict) and sandbox.get("allowed") is False


def _evaluation_unsafe(state: dict[str, Any]) -> bool:
    evaluation = state.get("evaluation")
    return isinstance(evaluation, dict) and evaluation.get("is_safe") is False
