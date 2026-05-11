from typing import Any

from app.services.tool_registry import BaseTool, ToolResult


class ComplianceCheckTool(BaseTool):
    name = "compliance_check"
    description = "Check whether an answer includes basic maintenance safety terms."

    _required_terms = ("断电", "停机", "防护", "风险")

    async def run(self, payload: dict[str, Any]) -> ToolResult:
        text = str(payload.get("answer") or payload.get("sop") or "")
        missing_terms = [term for term in self._required_terms if term not in text]
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "is_compliant": not missing_terms,
                "missing_terms": missing_terms,
                "checked_terms": list(self._required_terms),
                "risk_level": "low" if not missing_terms else "medium",
            },
        )
