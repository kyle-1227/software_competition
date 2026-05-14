from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.query import EvidenceItem, EvaluationResult, ToolCallItem

logger = logging.getLogger(__name__)

EVALUATOR_PROMPT = (
    "你是设备检修系统的质量评估器。评估诊断回答的质量。\n\n"
    "评估维度:\n"
    "- safety: 回答是否包含安全操作指导（停机、断电、防护、风险确认）\n"
    "- compliance: 是否满足检修合规要求\n"
    "- confidence: 证据支撑度和建议适当性\n"
    "- issues: 缺失的证据或安全要求\n"
    "- feedback: 如果置信度低于 0.7 或有 issues，给出具体改进建议\n\n"
    "返回 JSON:\n"
    '{"is_safe": true/false, '
    '"is_compliant": true/false, '
    '"confidence": 0.0-1.0, '
    '"issues": ["问题1", "问题2"], '
    '"feedback": "改进建议（可选）"}\n'
    "只返回 JSON，不要其他文字。"
)


class LLMEvaluator:
    """LLM-as-Judge 评估器：用 LLM 评估回答质量并提供改进反馈。

    当 LLM 不可用时，fallback 到旧 Evaluator（规则引擎）。
    """

    def __init__(
        self,
        llm_client: Any | None = None,
        fallback_evaluator: Any | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._fallback = fallback_evaluator

    async def evaluate(
        self,
        answer: str,
        evidence: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        sop: list[str] | None = None,
        sandbox_result: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        if self._llm_client is not None:
            try:
                return await self._llm_evaluate(
                    answer, evidence, tool_calls, sop, sandbox_result
                )
            except Exception as exc:
                logger.warning("LLM evaluator failed, using fallback: %s", exc)

        return self._fallback_evaluate(answer, evidence, tool_calls, sop, sandbox_result)

    async def _llm_evaluate(
        self,
        answer: str,
        evidence: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        sop: list[str] | None = None,
        sandbox_result: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        context: dict[str, Any] = {
            "answer": answer[:2000],
            "evidence": [_compact_evidence(item) for item in evidence[:5]],
            "tool_calls": [_compact_tool_call(item) for item in tool_calls[-5:]],
        }
        if sop:
            context["sop"] = sop[:5]
        if sandbox_result:
            context["sandbox_result"] = {
                "allowed": sandbox_result.get("allowed"),
                "return_code": sandbox_result.get("return_code"),
                "error": sandbox_result.get("error"),
            }

        try:
            response = await self._llm_client.generate_json(
                EVALUATOR_PROMPT, context
            )
        except Exception:
            response = await self._llm_client.generate_text(
                EVALUATOR_PROMPT, context
            )

        text = getattr(response, "text", "")
        if not text:
            return self._fallback_evaluate(answer, evidence, tool_calls, sop, sandbox_result)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return self._fallback_evaluate(answer, evidence, tool_calls, sop, sandbox_result)

        return EvaluationResult(
            is_safe=bool(data.get("is_safe", False)),
            is_compliant=bool(data.get("is_compliant", False)),
            confidence=float(data.get("confidence", 0.5)),
            issues=data.get("issues", []),
            feedback=data.get("feedback"),
        )

    def _fallback_evaluate(
        self,
        answer: str,
        evidence: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        sop: list[str] | None = None,
        sandbox_result: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        if self._fallback is not None:
            from app.schemas.query import EvidenceItem as EI, ToolCallItem as TCI
            ev_items = [EI(**item) if isinstance(item, dict) else item for item in evidence]
            tc_items = [TCI(**item) if isinstance(item, dict) else item for item in tool_calls]
            result = self._fallback.evaluate(answer, ev_items, tc_items)
            if isinstance(result, EvaluationResult):
                return result
            return EvaluationResult(**result.model_dump(mode="json"))

        # Ultimate fallback: inline heuristic
        unsafe_terms = ("直接拆卸", "跳过防护", "带电操作")
        safety_terms = ("断电", "停机", "防护", "风险")
        is_safe = not any(term in answer for term in unsafe_terms)
        is_compliant = all(term in answer for term in safety_terms)
        issues: list[str] = []
        if not is_safe:
            issues.append("回答包含潜在不安全操作表述。")
        if not is_compliant:
            issues.append("回答缺少必要的安全或合规提示。")
        if not evidence:
            issues.append("缺少手册证据支撑。")

        confidence = 0.35
        if evidence:
            confidence += 0.35
        if is_safe:
            confidence += 0.15
        if is_compliant:
            confidence += 0.15

        feedback = None
        if issues:
            feedback = "；".join(issues)

        return EvaluationResult(
            is_safe=is_safe,
            is_compliant=is_compliant,
            confidence=min(confidence, 1.0),
            issues=issues,
            feedback=feedback,
        )


def _compact_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "page": item.get("page"),
        "snippet": str(item.get("snippet", ""))[:200],
        "score": item.get("score"),
    }


def _compact_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": item.get("tool_name"),
        "status": item.get("status"),
    }
