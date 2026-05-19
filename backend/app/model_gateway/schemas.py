from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ModelTask = Literal[
    "planning",
    "answer_generation",
    "guardrail",
    "evaluation",
    "tool_generation",
    "embedding",
    "vision",
    "generic",
]


class ModelRequest(BaseModel):
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)
    task: ModelTask = "generic"
    response_format: Literal["text", "json"] = "text"
    trace_id: str | None = None
    run_id: str | None = None


class ModelGatewayResponse(BaseModel):
    text: str = ""
    model: str | None = None
    usage: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    provider: str = "model_gateway"
    task: ModelTask = "generic"
    raw: Any | None = None
