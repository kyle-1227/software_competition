from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.guardrails import GuardrailResult

logger = logging.getLogger(__name__)

_REQUIRED_SAFETY_TERMS = ("停机", "断电", "防护", "风险")
_FORBIDDEN_TERMS = ("直接拆卸", "跳过防护", "带电操作", "带电检修")
_SAFETY_HEADER = (
    "【安全提醒】操作前请先停机并断电，佩戴防护用品，"
    "确认现场风险受控后再进行检查。\n\n"
)
_VERIFY_SUFFIX = "\n\n【提示】以上内容中的页码和参数值请以实际手册为准，必要时由具备资质的人员复核。"

OUTPUT_GUARD_PROMPT = (
    "你是设备检修系统的输出安全网关。检查诊断回答是否安全。\n\n"
    "检查项:\n"
    "- 是否包含停机、断电、防护、风险确认等安全术语\n"
    "- 是否有直接拆卸带电操作等危险表述\n"
    "- 是否有手册中未提到的虚构页码或参数值\n\n"
    "返回 JSON:\n"
    '{"passed": true/false, "risk_level": "low"/"medium"/"high", '
    '"issues": ["问题描述"], '
    '"should_add_safety_header": true/false, '
    '"should_add_verify_suffix": true/false}\n'
    "只返回 JSON，不要其他文字。"
)


class OutputGuardrail:
    """出口护栏：在答案返回给用户前进行最终安全检查。

    与 current compliance_node 的关键区别：OutputGuardrail 会主动修复答案。
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

    async def check(
        self,
        answer: str,
        evaluation: dict[str, Any] | None = None,
    ) -> GuardrailResult:
        issues: list[str] = []
        should_add_header = False
        should_add_suffix = False

        missing = [t for t in _REQUIRED_SAFETY_TERMS if t not in answer]
        if missing:
            issues.append("缺少安全术语: " + ", ".join(missing))
            should_add_header = True

        for term in _FORBIDDEN_TERMS:
            if term in answer:
                issues.append("包含潜在不安全表述: " + term)
                should_add_header = True

        if evaluation:
            eval_issues = evaluation.get("issues", [])
            if eval_issues:
                issues.extend(eval_issues)
            if not evaluation.get("is_safe", True):
                should_add_header = True
            if evaluation.get("confidence", 1.0) < 0.6:
                should_add_suffix = True

        if self._llm_client is not None:
            try:
                llm_result = await self._llm_check(answer)
                if llm_result:
                    if llm_result.get("should_add_header"):
                        should_add_header = True
                    if llm_result.get("should_add_suffix"):
                        should_add_suffix = True
                    llm_issues = llm_result.get("issues", [])
                    if llm_issues:
                        issues.extend(llm_issues)
            except Exception as exc:
                logger.warning("Output guardrail LLM check failed: %s", exc)

        if issues:
            has_forbidden = any(term in answer for term in _FORBIDDEN_TERMS)
            risk_level = "high" if has_forbidden else ("medium" if should_add_header else "low")

            return GuardrailResult(
                passed=False,
                reason="; ".join(issues),
                risk_level=risk_level,
                blocked=has_forbidden,
            )

        return GuardrailResult(passed=True, risk_level="low")

    async def _llm_check(self, answer: str) -> dict[str, Any] | None:
        context = {"answer": answer[:3000]}
        try:
            response = await self._llm_client.generate_json(
                OUTPUT_GUARD_PROMPT, context
            )
        except Exception:
            response = await self._llm_client.generate_text(
                OUTPUT_GUARD_PROMPT, context
            )

        text = getattr(response, "text", "")
        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def apply_fixes(self, answer: str, result: GuardrailResult) -> str:
        """Apply auto-fixes based on guardrail result."""
        if result.passed:
            return answer

        modified = answer
        issues = result.reason or ""

        if "缺少安全术语" in issues or "不安全" in issues:
            if _SAFETY_HEADER.strip() not in modified:
                modified = _SAFETY_HEADER + modified

        if "虚构" in issues or "confidence" in issues.lower():
            modified = modified.rstrip() + _VERIFY_SUFFIX

        for term in _FORBIDDEN_TERMS:
            if term in modified:
                modified = modified.replace(term, term + "【需人工审核】")

        return modified
