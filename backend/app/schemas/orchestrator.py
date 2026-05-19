from pydantic import BaseModel, Field


class OrchestratorDecision(BaseModel):
    intent: str  # fault_triage | sop_guidance | ai_coding | mixed | general
    workers: list[str] = Field(default_factory=list)
    reasoning: str = ""
    priority: str = "safety_first"  # safety_first | diagnosis_first
    dynamic_plan: list[dict[str, str]] = Field(default_factory=list)
    task_plan: dict | None = None
    risk_level: str = "low"
    allowed_tools: list[str] = Field(default_factory=list)
