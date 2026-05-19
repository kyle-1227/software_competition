from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]


class PlannedTask(BaseModel):
    step: str
    worker: str
    action: str
    status: str = "pending"
    required_tools: list[str] = Field(default_factory=list)

    def to_dynamic_step(self) -> dict[str, str]:
        return {
            "step": self.step,
            "action": self.action,
            "status": self.status,
        }


class TaskPlan(BaseModel):
    intent: str = "general"
    tasks: list[PlannedTask] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    priority: str = "safety_first"
    reasoning: str = ""

    @property
    def workers(self) -> list[str]:
        workers: list[str] = []
        for task in self.tasks:
            if task.worker and task.worker not in workers:
                workers.append(task.worker)
        return workers or ["fault_triage"]

    def to_dynamic_plan(self) -> list[dict[str, str]]:
        return [task.to_dynamic_step() for task in self.tasks]
