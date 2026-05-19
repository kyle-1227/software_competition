from __future__ import annotations


class RiskClassifier:
    high_risk_terms = (
        "刷写",
        "ECU",
        "高压",
        "燃油",
        "制动",
        "安全气囊",
        "bypass",
        "disable safety",
        "shutdown",
        "format",
        "rm -rf",
    )
    medium_risk_terms = (
        "拆",
        "更换",
        "调整",
        "正时",
        "链条",
        "气门",
        "压缩压力",
        "sandbox",
        "shell",
        "powershell",
        "sql",
    )

    def classify(self, question: str, workers: list[str] | None = None) -> str:
        text = question.lower()
        if any(term.lower() in text for term in self.high_risk_terms):
            return "high"
        if "ai_coding" in (workers or []):
            return "medium"
        if any(term.lower() in text for term in self.medium_risk_terms):
            return "medium"
        return "low"

    def allowed_tools(self, workers: list[str], risk_level: str) -> list[str]:
        tools: list[str] = []
        for worker in workers:
            if worker in {"fault_triage", "sop_guidance"}:
                tools.extend(["manual_lookup", "compliance_check"])
            elif worker == "ai_coding":
                tools.append("ai_coding")
                if risk_level != "high":
                    tools.append("sandbox_execute")
        if risk_level == "high":
            tools = [tool for tool in tools if tool != "sandbox_execute"]
            tools.append("human_approval")
        return list(dict.fromkeys(tools))
