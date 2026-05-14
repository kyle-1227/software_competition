from __future__ import annotations

from typing import Any

from app.services.skills_loader import SkillsLoader
from app.services.workers.base import BaseWorker


class FaultTriageWorker(BaseWorker):
    """故障诊断 Worker: retrieve → diagnose → verify。

    加载 `skills/fault_triage/` 下的 skill.md 和 AGENT.md 作为运行时配置。
    """

    name = "fault_triage"
    description = "故障诊断、原因分析、症状匹配、参数查询"
    tools = ["manual_lookup", "compliance_check"]

    def __init__(self) -> None:
        self._skill_def = SkillsLoader.load("fault_triage")

    async def execute(
        self, state: dict[str, Any], services: Any
    ) -> dict[str, Any]:
        question = state.get("question", "")
        device_name = state.get("device_name")
        device_model = state.get("device_model")

        # 1. Call manual_lookup to retrieve evidence
        result = await services.tool_registry.execute(
            "manual_lookup",
            {
                "question": question,
                "device_name": device_name,
                "device_model": device_model,
            },
        )

        evidence: list[dict[str, Any]] = []
        if result.success and isinstance(result.data, list):
            evidence = [item for item in result.data if isinstance(item, dict)]

        tool_calls = [
            {
                "tool_name": result.tool_name,
                "input": {"question": question},
                "output": result.data if result.success else {"error": result.error},
                "status": "success" if result.success else "failed",
                "duration_ms": result.metadata.get("duration_ms"),
            }
        ]

        # 2. Preliminary diagnosis: extract top evidence sections
        symptom_analysis = self._analyse_symptoms(question, evidence)

        return {
            "evidence": evidence,
            "tool_calls": tool_calls,
            "worker_outputs": [
                {
                    "worker": self.name,
                    "symptom_analysis": symptom_analysis,
                    "evidence_count": len(evidence),
                    "warnings": [] if evidence else ["未检索到手册证据"],
                }
            ],
            "warnings": state.get("warnings", [])
            + ([] if evidence else ["未检索到手册证据"]),
        }

    def _analyse_symptoms(
        self, question: str, evidence: list[dict[str, Any]]
    ) -> str:
        if not evidence:
            return "未检索到足够的手册证据，建议补充故障现象或设备型号。"

        top_sections = []
        for item in evidence[:3]:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            section = metadata.get("section") or metadata.get("chapter") or "相关章节"
            page = item.get("page")
            page_ref = f"P.{page}" if page is not None else "P.-"
            top_sections.append(f"{page_ref} {section}")

        sections_str = "、".join(top_sections)
        return f"故障问题: {question}。匹配到 {len(evidence)} 条手册证据，涉及: {sections_str}。"
