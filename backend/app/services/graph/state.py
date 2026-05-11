from typing import Any, TypedDict


class HarnessState(TypedDict, total=False):
    question: str
    device_name: str | None
    device_model: str | None
    session_id: str
    trace_id: str | None
    memory: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    needs_ai_coding: bool
    ai_coding: dict[str, Any] | None
    sandbox_result: dict[str, Any] | None
    answer: str
    evaluation: dict[str, Any] | None
    sop: list[str]
    llm_model: str | None
    llm_usage: dict[str, Any] | None
    response: Any
    errors: list[str]
    warnings: list[str]
