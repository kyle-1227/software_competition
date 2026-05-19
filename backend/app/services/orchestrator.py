from __future__ import annotations

import json
import logging
from typing import Any

from app.planning.planner import Planner
from app.planning.task_plan import TaskPlan
from app.schemas.orchestrator import OrchestratorDecision

logger = logging.getLogger(__name__)

ORCHESTRATOR_PROMPT = (
    "You are the task orchestrator for a maintenance assistant. "
    "Classify the user request and choose workers. Return only JSON with "
    "intent, workers, reasoning, and priority."
)

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "fault_triage": [
        "fault",
        "diagnose",
        "diagnosis",
        "reason",
        "symptom",
        "cannot",
        "won't start",
        "check",
        "parameter",
        "engine",
        "idle",
        "misfire",
        "故障",
        "无法",
        "异常",
        "原因",
        "排查",
        "检查",
        "参数",
        "启动",
        "怠速",
        "回火",
    ],
    "sop_guidance": [
        "sop",
        "procedure",
        "step",
        "steps",
        "process",
        "maintenance",
        "replace",
        "install",
        "remove",
        "safety",
        "步骤",
        "流程",
        "规程",
        "操作",
        "拆",
        "安装",
        "更换",
        "维护",
        "保养",
        "安全",
    ],
    "ai_coding": [
        "script",
        "code",
        "python",
        "sql",
        "shell",
        "powershell",
        "脚本",
        "代码",
        "编程",
        "自动化",
    ],
}


class Orchestrator:
    """LLM/planner-backed worker orchestrator with deterministic fallback."""

    def __init__(
        self,
        llm_client: Any | None = None,
        model_gateway: Any | None = None,
        planner: Planner | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._model_gateway = model_gateway
        self._planner = planner or (
            Planner(model_gateway=model_gateway) if model_gateway is not None else None
        )

    def classify_keywords(self, question: str) -> OrchestratorDecision:
        """Fast keyword-based classification (fallback path)."""
        lowered = question.lower()
        workers: list[str] = []
        intents: list[str] = []

        for intent, keywords in _INTENT_KEYWORDS.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                intents.append(intent)
                if intent not in workers:
                    workers.append(intent)

        if not workers:
            workers = ["fault_triage"]
            intents = ["general"]

        intent = intents[0] if len(intents) == 1 else "mixed"
        priority = "diagnosis_first" if "ai_coding" in workers else "safety_first"
        return OrchestratorDecision(
            intent=intent,
            workers=workers,
            reasoning=f"keyword fallback matched {', '.join(intents)}",
            priority=priority,
            dynamic_plan=self._build_dynamic_plan(intent, workers),
            risk_level="medium" if "ai_coding" in workers else "low",
            allowed_tools=self._allowed_tools(workers),
        )

    async def classify_and_plan(
        self,
        question: str,
        device_name: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> OrchestratorDecision:
        """Main entrypoint: planner first, legacy LLM second, keywords last."""
        if self._planner is not None:
            try:
                task_plan = await self._planner.plan(
                    question,
                    device_name=device_name,
                    history=history,
                )
                return self._decision_from_task_plan(task_plan)
            except Exception as exc:
                logger.warning("Orchestrator planner failed, using legacy path: %s", exc)

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
            context["history"] = history[-3:]

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

        valid_workers = [
            worker
            for worker in workers
            if worker in {"fault_triage", "sop_guidance", "ai_coding"}
        ]
        if not valid_workers:
            valid_workers = ["fault_triage"]

        return OrchestratorDecision(
            intent=str(intent),
            workers=valid_workers,
            reasoning=str(reasoning or "LLM classification"),
            priority=str(priority),
            dynamic_plan=self._build_dynamic_plan(str(intent), valid_workers),
            risk_level="medium" if "ai_coding" in valid_workers else "low",
            allowed_tools=self._allowed_tools(valid_workers),
        )

    def _decision_from_task_plan(self, task_plan: TaskPlan) -> OrchestratorDecision:
        return OrchestratorDecision(
            intent=task_plan.intent,
            workers=task_plan.workers,
            reasoning=task_plan.reasoning,
            priority=task_plan.priority,
            dynamic_plan=task_plan.to_dynamic_plan(),
            task_plan=task_plan.model_dump(mode="json"),
            risk_level=task_plan.risk_level,
            allowed_tools=task_plan.allowed_tools,
        )

    def _build_dynamic_plan(
        self,
        intent: str,
        workers: list[str],
    ) -> list[dict[str, str]]:
        del intent
        plan: list[dict[str, str]] = [
            {
                "step": "intake",
                "action": "Normalize input, load memory, and bind trace context.",
                "status": "pending",
            }
        ]
        for worker in workers:
            if worker == "fault_triage":
                action = "Retrieve manual evidence and analyze likely fault causes."
            elif worker == "sop_guidance":
                action = "Build safety-first procedure from manual evidence."
            elif worker == "ai_coding":
                action = "Generate a reviewable diagnostic script through tool broker."
            else:
                action = "Execute planned worker."
            plan.append({"step": worker, "action": action, "status": "pending"})
        plan.append(
            {
                "step": "evaluate",
                "action": "Evaluate evidence, safety, and compliance.",
                "status": "pending",
            }
        )
        plan.append(
            {
                "step": "answer",
                "action": "Generate final answer from verified evidence and tool results.",
                "status": "pending",
            }
        )
        return plan

    def _allowed_tools(self, workers: list[str]) -> list[str]:
        tools: list[str] = []
        for worker in workers:
            if worker in {"fault_triage", "sop_guidance"}:
                tools.extend(["manual_lookup", "compliance_check"])
            elif worker == "ai_coding":
                tools.extend(["ai_coding", "sandbox_execute"])
        return list(dict.fromkeys(tools))
