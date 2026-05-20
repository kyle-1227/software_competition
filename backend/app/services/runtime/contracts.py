from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


RuntimeStatus = Literal[
    "accepted",
    "running",
    "waiting_for_approval",
    "succeeded",
    "failed",
    "cancelled",
    "timeout",
]
RuntimeStepStatus = Literal["pending", "running", "completed", "failed", "skipped"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimePolicy(BaseModel):
    max_steps: int = 16
    timeout_seconds: float = 120.0
    max_tool_retries: int = 5
    max_retrieval_retries: int = 2
    max_answer_regenerations: int = 2
    high_risk_requires_approval: bool = True
    allow_side_effects: bool = False


class RuntimeSecurity(BaseModel):
    redaction_enabled: bool = True
    sandbox_profile: str = "restricted"
    audit_enabled: bool = True
    allowed_tool_scopes: list[str] = Field(
        default_factory=lambda: ["manual_lookup", "compliance_check", "ai_coding"]
    )


class RuntimeRequest(BaseModel):
    request_id: str
    source: str = "api.query"
    question: str
    device_name: str | None = None
    device_model: str | None = None
    session_id: str
    received_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeStep(BaseModel):
    name: str
    status: RuntimeStepStatus = "pending"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeEvent(BaseModel):
    type: str
    message: str
    timestamp: datetime = Field(default_factory=utc_now)
    data: dict[str, Any] = Field(default_factory=dict)


class RuntimeState(BaseModel):
    request: RuntimeRequest
    status: RuntimeStatus = "accepted"
    policy: RuntimePolicy = Field(default_factory=RuntimePolicy)
    security: RuntimeSecurity = Field(default_factory=RuntimeSecurity)
    steps: list[RuntimeStep] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    harness_state: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None


class RuntimeResult(BaseModel):
    request_id: str
    status: RuntimeStatus
    trace_id: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    harness_state: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime = Field(default_factory=utc_now)
