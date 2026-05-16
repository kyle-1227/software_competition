from __future__ import annotations

import json
import logging
from typing import Any

from app.schemas.orchestrator import OrchestratorDecision

logger = logging.getLogger(__name__)

ORCHESTRATOR_PROMPT = (
    "你是设备检修智能系统的任务编排器。分析用户问题并决定需要调用哪些 Worker。\n\n"
    "可用的 Worker:\n"
    "- fault_triage: 故障诊断、原因分析、症状匹配、参数查询\n"
    "- sop_guidance: 生成检修步骤、安全操作流程、标准作业指引\n"
    "- ai_coding: 生成诊断脚本（Python/SQL/Shell）\n\n"
    "规则:\n"
    "- 故障/症状/原因类问题 → fault_triage\n"
    "- 操作步骤/流程/规程类问题 → sop_guidance\n"
    "- 脚本/代码/编程类问题 → ai_coding\n"
    "- 复杂问题可以同时选择多个 workers\n\n"
    "返回 JSON:\n"
    '{"intent": "fault_triage"|"sop_guidance"|"ai_coding"|"mixed"|"general", '
    '"workers": ["fault_triage"], '
    '"reasoning": "简短推理", '
    '"priority": "safety_first"|"diagnosis_first"}\n'
    "不要返回其他文字，只返回 JSON。"
)

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "fault_triage": [
        "故障", "无法", "不工作", "坏了", "异常", "怠速不稳", "回火",
        "启动困难", "原因", "怎么办", "哪里", "怎么修", "修理", "诊断",
        "排查", "检查", "多少", "参数", "标准值", "范围", "数值",
        "间隙", "压力", "火花塞", "气门", "压缩", "排气管", "热车",
    ],
    "sop_guidance": [
        "步骤", "流程", "规程", "操作", "拆卸", "安装", "更换", "维护",
        "保养", "SOP", "指引", "标准作业", "安全", "怎么拆", "怎么装",
    ],
    "ai_coding": [
        "脚本", "代码", "编程", "script", "code", "python",
        "生成.*程序", "写.*程序", "sql", "自动化",
    ],
}


class Orchestrator:
    """LLM 驱动的动态编排器，分析意图并选择 Worker。

    当 LLM 不可用时，使用关键词分类作为 fallback。
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

    def classify_keywords(self, question: str) -> OrchestratorDecision:
        """Fast keyword-based classification (fallback path)."""
        workers: list[str] = []
        intents: list[str] = []

        for intent, keywords in _INTENT_KEYWORDS.items():
            if any(kw in question.lower() for kw in keywords):
                intents.append(intent)
                if intent not in workers:
                    workers.append(intent)

        if not workers:
            workers = ["fault_triage"]
            intents = ["general"]

        intent = intents[0] if len(intents) == 1 else "mixed"

        if "ai_coding" in workers:
            priority = "diagnosis_first"
        else:
            priority = "safety_first"

        return OrchestratorDecision(
            intent=intent,
            workers=workers,
            reasoning=f"关键词分类: 匹配 {', '.join(intents)}",
            priority=priority,
            dynamic_plan=self._build_dynamic_plan(intent, workers),
        )

    async def classify_and_plan(
        self,
        question: str,
        device_name: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> OrchestratorDecision:
        """主入口：LLM 分类，失败时 fallback 到关键词分类。"""
        if self._llm_client is not None:
            try:
                return await self._llm_classify(question, device_name, history)
            except Exception as exc:
                logger.warning("Orchestrator LLM classify failed, using keywords: %s", exc)

        return self.classify_keywords(question)

    async def _llm_classify(
        self,
        question: str,
        device_name: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> OrchestratorDecision:
        context: dict[str, Any] = {"question": question}
        if device_name:
            context["device_name"] = device_name
        if history:
            context["history"] = history[-3:]  # last 3 entries for context

        response = await self._llm_client.generate_json(ORCHESTRATOR_PROMPT, context)
        text = getattr(response, "text", "")
        if not text:
            return self.classify_keywords(question)
        warnings = getattr(response, "warnings", [])
        if any("fallback" in str(warning).lower() for warning in warnings):
            return self.classify_keywords(question)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return self.classify_keywords(question)
        if "intent" not in data and "workers" not in data:
            return self.classify_keywords(question)

        intent = data.get("intent", "general")
        workers = data.get("workers", ["fault_triage"])
        reasoning = data.get("reasoning", "")
        priority = data.get("priority", "safety_first")

        if not isinstance(workers, list) or not workers:
            workers = ["fault_triage"]

        valid_workers = [w for w in workers if w in {"fault_triage", "sop_guidance", "ai_coding"}]
        if not valid_workers:
            valid_workers = ["fault_triage"]

        return OrchestratorDecision(
            intent=intent,
            workers=valid_workers,
            reasoning=reasoning or "LLM 分类",
            priority=priority,
            dynamic_plan=self._build_dynamic_plan(intent, valid_workers),
        )

    def _build_dynamic_plan(
        self, intent: str, workers: list[str]
    ) -> list[dict[str, str]]:
        """根据意图和 worker 列表生成动态执行计划。"""
        plan: list[dict[str, str]] = []

        plan.append({"step": "intake", "action": "规范化输入、加载会话历史", "status": "pending"})

        for worker in workers:
            if worker == "fault_triage":
                plan.append({"step": "fault_triage", "action": "检索手册、分析故障原因", "status": "pending"})
            elif worker == "sop_guidance":
                plan.append({"step": "sop_guidance", "action": "生成标准作业步骤和安全检查项", "status": "pending"})
            elif worker == "ai_coding":
                plan.append({"step": "ai_coding", "action": "生成诊断脚本并在沙箱中执行", "status": "pending"})

        plan.append({"step": "evaluate", "action": "综合评估证据、安全性和合规性", "status": "pending"})
        plan.append({"step": "answer", "action": "生成最终诊断建议", "status": "pending"})

        return plan
