from __future__ import annotations

import json
import logging
from typing import Any

from app.planning.risk_classifier import RiskClassifier
from app.planning.task_plan import PlannedTask, TaskPlan

logger = logging.getLogger(__name__)

PLANNER_PROMPT = (
    "You are the production runtime planner for a maintenance assistant. "
    "Choose workers and allowed tools. Return only JSON with keys: "
    "intent, workers, risk_level, priority, reasoning, allowed_tools."
)

VALID_WORKERS = {"fault_triage", "sop_guidance", "ai_coding"}


class Planner:
    """LLM-first task planner with deterministic fallback."""

    def __init__(
        self,
        model_gateway: Any | None = None,
        risk_classifier: RiskClassifier | None = None,
    ) -> None:
        self._model_gateway = model_gateway
        self._risk_classifier = risk_classifier or RiskClassifier()

    async def plan(
        self,
        question: str,
        *,
        device_name: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> TaskPlan:
        if self._model_gateway is not None:
            try:
                response = await self._model_gateway.generate_json(
                    PLANNER_PROMPT,
                    {
                        "question": question,
                        "device_name": device_name,
                        "history": (history or [])[-3:],
                    },
                    task="planning",
                )
                warnings = getattr(response, "warnings", [])
                if not any("fallback" in str(item).lower() for item in warnings):
                    planned = self._from_llm_text(getattr(response, "text", ""))
                    if planned is not None:
                        return planned
            except Exception as exc:
                logger.warning("Planner model_gateway failed, using fallback: %s", exc)
        return self.fallback_plan(question)

    def fallback_plan(self, question: str) -> TaskPlan:
        lowered = question.lower()
        workers: list[str] = []
        if any(term in lowered for term in ("script", "code", "python", "sql", "shell")):
            workers.append("ai_coding")
        if any(term in question for term in ("步骤", "流程", "规程", "SOP", "拆", "装", "更换", "维护", "保养", "安全")):
            workers.append("sop_guidance")
        if not workers or any(term in question for term in ("故障", "无法", "异常", "原因", "排查", "检查", "参数", "怠速", "回火", "启动")):
            workers.insert(0, "fault_triage")
        workers = list(dict.fromkeys(worker for worker in workers if worker in VALID_WORKERS))
        intent = workers[0] if len(workers) == 1 else ("mixed" if workers else "general")
        risk_level = self._risk_classifier.classify(question, workers)
        allowed_tools = self._risk_classifier.allowed_tools(workers, risk_level)
        return self._build_plan(
            intent=intent,
            workers=workers or ["fault_triage"],
            risk_level=risk_level,
            allowed_tools=allowed_tools,
            priority="diagnosis_first" if "ai_coding" in workers else "safety_first",
            reasoning="deterministic planner fallback",
        )

    def _from_llm_text(self, text: str) -> TaskPlan | None:
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        workers = data.get("workers", [])
        if not isinstance(workers, list):
            workers = []
        workers = [str(worker) for worker in workers if str(worker) in VALID_WORKERS]
        if not workers:
            return None
        risk_level = str(data.get("risk_level") or self._risk_classifier.classify("", workers))
        if risk_level not in {"low", "medium", "high"}:
            risk_level = self._risk_classifier.classify("", workers)
        allowed_tools = data.get("allowed_tools")
        if not isinstance(allowed_tools, list):
            allowed_tools = self._risk_classifier.allowed_tools(workers, risk_level)
        return self._build_plan(
            intent=str(data.get("intent") or workers[0]),
            workers=workers,
            risk_level=risk_level,
            allowed_tools=[str(tool) for tool in allowed_tools],
            priority=str(data.get("priority") or "safety_first"),
            reasoning=str(data.get("reasoning") or "model gateway planner"),
        )

    def _build_plan(
        self,
        *,
        intent: str,
        workers: list[str],
        risk_level: str,
        allowed_tools: list[str],
        priority: str,
        reasoning: str,
    ) -> TaskPlan:
        tasks = [
            PlannedTask(
                step="intake",
                worker="runtime",
                action="Normalize request, load memory, and bind trace context.",
                required_tools=[],
            )
        ]
        for worker in workers:
            tasks.append(
                PlannedTask(
                    step=worker,
                    worker=worker,
                    action=self._worker_action(worker),
                    required_tools=[
                        tool
                        for tool in allowed_tools
                        if self._tool_belongs_to_worker(tool, worker)
                    ],
                )
            )
        if risk_level == "high":
            tasks.append(
                PlannedTask(
                    step="approval",
                    worker="runtime",
                    action="Require human approval before high-risk execution.",
                    required_tools=["human_approval"],
                )
            )
        tasks.append(
            PlannedTask(
                step="evaluate",
                worker="runtime",
                action="Evaluate evidence, safety, and compliance before final response.",
                required_tools=[],
            )
        )
        tasks.append(
            PlannedTask(
                step="answer",
                worker="runtime",
                action="Generate final answer only from verified evidence and tool results.",
                required_tools=[],
            )
        )
        return TaskPlan(
            intent=intent,
            tasks=tasks,
            allowed_tools=list(dict.fromkeys(allowed_tools)),
            risk_level=risk_level,  # type: ignore[arg-type]
            priority=priority,
            reasoning=reasoning,
        )

    def _worker_action(self, worker: str) -> str:
        return {
            "fault_triage": "Retrieve manual evidence and analyze likely fault causes.",
            "sop_guidance": "Build safety-first procedure from manual evidence.",
            "ai_coding": "Generate reviewable diagnostic script through the tool broker.",
        }.get(worker, "Execute planned worker.")

    def _tool_belongs_to_worker(self, tool: str, worker: str) -> bool:
        if worker in {"fault_triage", "sop_guidance"}:
            return tool in {"manual_lookup", "compliance_check"}
        if worker == "ai_coding":
            return tool in {"ai_coding", "sandbox_execute"}
        return False
