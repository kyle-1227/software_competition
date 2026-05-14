from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SpanKind(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    LLM = "llm"
    GUARDRAIL = "guardrail"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class TraceSpan(BaseModel):
    span_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list[TraceSpan] = Field(default_factory=list)


class Trace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    question: str
    root_span: TraceSpan
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
