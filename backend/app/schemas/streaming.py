from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StreamEventType(str, Enum):
    GUARDRAIL_PASSED = "guardrail_passed"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    INTENT_CLASSIFIED = "intent_classified"
    WORKER_STARTED = "worker_started"
    WORKER_COMPLETED = "worker_completed"
    EVIDENCE_RETRIEVED = "evidence_retrieved"
    ANSWER_GENERATING = "answer_generating"
    ANSWER_GENERATED = "answer_generated"
    EVALUATION_COMPLETE = "evaluation_complete"
    OUTPUT_GUARDRAIL_RESULT = "output_guardrail_result"
    FINAL_RESPONSE = "final_response"
    ERROR = "error"


class StreamEvent(BaseModel):
    type: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
