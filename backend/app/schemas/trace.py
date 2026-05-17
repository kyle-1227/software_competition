from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class SpanKind(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    LLM = "llm"
    GUARDRAIL = "guardrail"
    NODE = "node"
    RETRIEVER = "retriever"
    RERANKER = "reranker"
    EVALUATOR = "evaluator"
    SANDBOX = "sandbox"
    MEMORY = "memory"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class TraceStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


class TraceSpan(BaseModel):
    span_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str | None = None
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    duration_ms: float | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    error: str | None = None
    error_type: str | None = None
    attempt: int | None = None
    retry_count: int | None = None
    fallback_used: bool = False
    degraded: bool = False
    token_usage: dict[str, Any] | None = None
    cost_estimate: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list[TraceSpan] = Field(default_factory=list)


class Trace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str | None = None
    session_id: str
    user_id: str | None = None
    question: str
    normalized_question: str | None = None
    app_env: str | None = None
    app_version: str | None = None
    git_commit: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    reranker_model: str | None = None
    manual_id: str | None = None
    index_version: str | None = None
    index_sha256: str | None = None
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    status: TraceStatus = TraceStatus.RUNNING
    final_answer_hash: str | None = None
    root_span: TraceSpan
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    total_duration_ms: float | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> TraceStatus:
        if isinstance(value, TraceStatus):
            return value
        normalized = str(value or TraceStatus.RUNNING.value).lower()
        if normalized == "ok":
            return TraceStatus.SUCCESS
        try:
            return TraceStatus(normalized)
        except ValueError:
            return TraceStatus.ERROR
