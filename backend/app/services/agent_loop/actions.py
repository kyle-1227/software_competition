from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentLoopAction(StrEnum):
    CONTINUE = "CONTINUE"
    RETRY_TOOL = "RETRY_TOOL"
    RETRY_RETRIEVAL = "RETRY_RETRIEVAL"
    REWRITE_QUERY = "REWRITE_QUERY"
    REGENERATE_ANSWER = "REGENERATE_ANSWER"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    FINALIZE = "FINALIZE"
    FAIL_SAFE = "FAIL_SAFE"


class AgentLoopDecision(BaseModel):
    action: AgentLoopAction
    reason: str
    confidence: float = 1.0
    target: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
