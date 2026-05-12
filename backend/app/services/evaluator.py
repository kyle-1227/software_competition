from app.schemas.query import EvaluationResult, EvidenceItem, ToolCallItem


class Evaluator:
    """Local heuristic evaluator for safety, compliance, and confidence.

    The next implementation step is to pass the same inputs through
    prompts/evaluator_prompt.md and an LLM judge.
    """

    _unsafe_terms = ("直接拆卸", "跳过防护", "带电操作")
    _safety_terms = ("断电", "停机", "防护", "风险")

    def evaluate(
        self,
        answer: str,
        evidence: list[EvidenceItem],
        tool_calls: list[ToolCallItem],
    ) -> EvaluationResult:
        issues: list[str] = []
        is_safe = not any(term in answer for term in self._unsafe_terms)
        if not is_safe:
            issues.append("回答包含潜在不安全操作表述。")

        compliance_call = next(
            (call for call in tool_calls if call.tool_name == "compliance_check"),
            None,
        )
        is_compliant = all(term in answer for term in self._safety_terms)
        if isinstance(compliance_call, ToolCallItem) and isinstance(
            compliance_call.output, dict
        ):
            is_compliant = bool(compliance_call.output.get("is_compliant", is_compliant))

        if not is_compliant:
            issues.append("回答缺少必要的安全或合规提示。")
        if not evidence:
            issues.append("缺少手册证据支撑。")
        elif max(_evidence_score(item) for item in evidence) < 0.4:
            issues.append("手册证据匹配度较低，建议补充更具体的故障现象。")
        sandbox_call = next(
            (call for call in tool_calls if call.tool_name == "sandbox_execute"),
            None,
        )
        if sandbox_call and isinstance(sandbox_call.output, dict):
            if sandbox_call.output.get("allowed") is False:
                issues.append("AI Coding 脚本被 Sandbox 拒绝执行。")
            elif sandbox_call.output.get("return_code") not in (None, 0):
                issues.append("AI Coding 脚本执行返回非零状态。")

        confidence = 0.35
        if evidence:
            confidence += 0.35
        if is_safe:
            confidence += 0.15
        if is_compliant:
            confidence += 0.15

        return EvaluationResult(
            is_safe=is_safe,
            is_compliant=is_compliant,
            confidence=min(confidence, 1.0),
            issues=issues,
        )


def _evidence_score(item: EvidenceItem | dict) -> float:
    if isinstance(item, EvidenceItem):
        return float(item.score or 0.0)
    if isinstance(item, dict):
        return float(item.get("score") or 0.0)
    return 0.0
