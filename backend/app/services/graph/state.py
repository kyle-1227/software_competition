from typing import Any, TypedDict


class HarnessState(TypedDict, total=False):
    # -- existing fields (unchanged) --
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
    # -- Phase 0 new fields --
    guardrail_passed: bool
    intent: str
    task_plan: dict[str, Any]
    risk_level: str
    allowed_tools: list[str]
    _orchestrator_decision: Any
    worker_outputs: list[dict[str, Any]]
    evaluation_feedback: str
    iteration_count: int
    output_guardrail_issues: list[str]
    verification_passed: bool
    verification_issues: list[str]
    stream_events: list[dict[str, Any]]
    # -- Bounded Agent Loop fields --
    loop_decision_count: int
    loop_history: list[dict[str, Any]]
    tool_retry_counts: dict[str, int]
    retrieval_retry_count: int
    answer_regeneration_count: int
    degradation_events: list[dict[str, Any]]
    requires_human_approval: bool
    approval_reason: str | None
    clarification_question: str | None
    fail_safe_reason: str | None
    _agent_loop_decision: dict[str, Any]
    # -- Production Runtime Contract fields --
    runtime_request: dict[str, Any]
    runtime_contract: dict[str, Any]
    runtime_events: list[dict[str, Any]]
    runtime_step_count: int
