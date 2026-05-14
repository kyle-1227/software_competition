from __future__ import annotations

from typing import Any

from app.services.skills_loader import SkillsLoader
from app.services.workers.base import BaseWorker


class SOPGuidanceWorker(BaseWorker):
    """SOP 生成 Worker: retrieve → generate steps → safety check。

    加载 `skills/sop_guidance/` 下的 skill.md 和 AGENT.md。
    """

    name = "sop_guidance"
    description = "生成检修步骤、安全操作流程、标准作业指引"
    tools = ["manual_lookup", "compliance_check"]

    def __init__(self) -> None:
        self._skill_def = SkillsLoader.load("sop_guidance")

    async def execute(
        self, state: dict[str, Any], services: Any
    ) -> dict[str, Any]:
        question = state.get("question", "")
        evidence = state.get("evidence", [])

        # If no evidence in state, retrieve it
        if not evidence:
            result = await services.tool_registry.execute(
                "manual_lookup",
                {
                    "question": question,
                    "device_name": state.get("device_name"),
                    "device_model": state.get("device_model"),
                },
            )
            if result.success and isinstance(result.data, list):
                evidence = [item for item in result.data if isinstance(item, dict)]

        sop_steps = self._build_sop_steps(question, evidence)
        safety_prerequisites = self._build_safety_prerequisites()

        return {
            "worker_outputs": [
                {
                    "worker": self.name,
                    "sop_steps": sop_steps,
                    "safety_prerequisites": safety_prerequisites,
                    "stop_conditions": [
                        "发现手册未记载的异常现象时立即停止操作",
                        "测量值超出标准范围时停止并记录",
                    ],
                    "evidence_count": len(evidence),
                }
            ],
            "sop": sop_steps,
        }

    def _build_sop_steps(
        self, question: str, evidence: list[dict[str, Any]]
    ) -> list[str]:
        steps = [
            "停机并断电，确认设备处于安全状态。",
            "佩戴防护用品，检查现场风险。",
        ]

        if evidence:
            for item in evidence[:3]:
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                block_type = metadata.get("block_type", "")
                snippet = str(item.get("snippet", ""))[:120]

                if "检查" in block_type or "测量" in block_type:
                    steps.append(f"按手册执行: {snippet}")
                elif "调整" in block_type:
                    steps.append(f"调整步骤: {snippet}")

        steps.append("记录现象、测量值和处理步骤。")
        steps.append("必要时提交知识审核并更新维护记录。")

        return steps

    def _build_safety_prerequisites(self) -> list[str]:
        return [
            "确认设备已完全冷却至室温",
            "使用绝缘工具和符合标准的测量仪表",
            "断开设备主电源并挂牌上锁",
            "保持工作区域通风良好",
        ]
