from typing import Annotated, Any

from pydantic import BaseModel, Field
from pydantic import StringConstraints


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class QueryRequest(BaseModel):
    question: NonEmptyStr = Field(description="故障问题或任务请求。")
    device_name: str | None = None
    device_model: str | None = None
    session_id: str | None = None


class EvidenceItem(BaseModel):
    source: str
    page: int | None = None
    snippet: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    step: str
    status: str


class ToolCallItem(BaseModel):
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | list[dict[str, Any]] | str | None = None
    status: str
    duration_ms: int | None = None


class SandboxResult(BaseModel):
    language: str
    allowed: bool
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: int | None = None


class EvaluationResult(BaseModel):
    is_safe: bool
    is_compliant: bool
    confidence: float
    issues: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    answer: str
    plan: list[PlanStep]
    evidence: list[EvidenceItem]
    tool_calls: list[ToolCallItem] = Field(default_factory=list)
    evaluation: EvaluationResult | None = None
    trace_id: str | None = None
    sop: list[str] = Field(default_factory=list)
    memory: list[dict[str, Any]] = Field(default_factory=list)
    ai_coding: dict[str, Any] | None = None
    llm_usage: dict[str, Any] | None = None
    llm_model: str | None = None
