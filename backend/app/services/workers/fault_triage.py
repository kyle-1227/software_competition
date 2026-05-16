from __future__ import annotations

from typing import Any

from app.services.agent_loop.retry import execute_tool_with_retry
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

        # 1. Call manual_lookup to retrieve evidence with bounded retry.
        retry_result = await execute_tool_with_retry(
            services.tool_registry,
            "manual_lookup",
            {
                "question": question,
                "device_name": device_name,
                "device_model": device_model,
                "trace_id": state.get("trace_id"),
            },
            trace_store=getattr(services, "trace_store", None),
            trace_id=state.get("trace_id"),
        )
        result = retry_result.result

        evidence: list[dict[str, Any]] = []
        if result is not None and result.success and isinstance(result.data, list):
            evidence = [item for item in result.data if isinstance(item, dict)]

        degraded_warnings = (
            ["manual_lookup 连续失败 5 次，已降级为证据不足模式"]
            if retry_result.degraded
            else []
        )

        # 2. Preliminary diagnosis: extract top evidence sections
        symptom_analysis = self._analyse_symptoms(question, evidence)
        evidence_warnings = [] if evidence else ["未检索到手册证据"]

        return {
            "evidence": evidence,
            "tool_calls": retry_result.tool_calls,
            "worker_outputs": [
                {
                    "worker": self.name,
                    "symptom_analysis": symptom_analysis,
                    "evidence_count": len(evidence),
                    "warnings": degraded_warnings + evidence_warnings,
                    "degraded": retry_result.degraded,
                    "retry_attempts": retry_result.attempts,
                }
            ],
            "degraded": retry_result.degraded,
            "retry_attempts": retry_result.attempts,
            "degradation_events": retry_result.degradation_events,
            "warnings": state.get("warnings", [])
            + degraded_warnings
            + evidence_warnings,
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
