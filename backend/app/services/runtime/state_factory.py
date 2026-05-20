from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.schemas.query import QueryRequest
from app.services.runtime.contracts import (
    RuntimeEvent,
    RuntimePolicy,
    RuntimeRequest,
    RuntimeSecurity,
    RuntimeState,
)
from app.services.tracing.serializers import sanitize_trace_dict


class RuntimeStateFactory:
    """Builds the production runtime envelope around the public query schema."""

    def from_query_request(
        self,
        payload: QueryRequest,
        *,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeState:
        session_id = (
            payload.session_id
            or payload.device_model
            or payload.device_name
            or "default"
        )
        runtime_request = RuntimeRequest(
            request_id=request_id or str(uuid4()),
            question=payload.question,
            device_name=payload.device_name,
            device_model=payload.device_model,
            session_id=session_id,
            metadata=sanitize_trace_dict(metadata or {}),
        )
        policy = RuntimePolicy(
            max_steps=max(1, int(getattr(settings, "runtime_max_steps", 16))),
            timeout_seconds=float(getattr(settings, "runtime_timeout_seconds", 120.0)),
            max_tool_retries=int(getattr(settings, "agent_loop_max_tool_retries", 5)),
            max_retrieval_retries=int(
                getattr(settings, "agent_loop_max_retrieval_retries", 2)
            ),
            max_answer_regenerations=int(
                getattr(settings, "agent_loop_max_answer_regenerations", 2)
            ),
            high_risk_requires_approval=bool(
                getattr(settings, "agent_loop_high_risk_requires_approval", True)
            ),
        )
        security = RuntimeSecurity()
        state = RuntimeState(
            request=runtime_request,
            policy=policy,
            security=security,
            events=[
                RuntimeEvent(
                    type="runtime.accepted",
                    message="Runtime request accepted",
                    data={"source": runtime_request.source},
                )
            ],
        )
        state.harness_state = self.to_harness_state(state)
        return state

    def to_harness_state(self, runtime_state: RuntimeState) -> dict[str, Any]:
        request = runtime_state.request
        return {
            "question": request.question,
            "device_name": request.device_name,
            "device_model": request.device_model,
            "session_id": request.session_id,
            "trace_id": None,
            "memory": [],
            "plan": [],
            "evidence": [],
            "tool_calls": [],
            "needs_ai_coding": False,
            "ai_coding": None,
            "sandbox_result": None,
            "answer": "",
            "evaluation": None,
            "sop": [],
            "llm_model": None,
            "llm_usage": None,
            "response": None,
            "errors": [],
            "warnings": [],
            "guardrail_passed": True,
            "iteration_count": 0,
            "loop_decision_count": 0,
            "loop_history": [],
            "tool_retry_counts": {},
            "retrieval_retry_count": 0,
            "answer_regeneration_count": 0,
            "degradation_events": [],
            "requires_human_approval": False,
            "approval_reason": None,
            "approval": None,
            "approval_decision": None,
            "approved_approval_id": None,
            "approval_scope_hash": None,
            "status": "completed",
            "clarification_question": None,
            "fail_safe_reason": None,
            "runtime_request": request.model_dump(mode="json"),
            "runtime_contract": {
                "request_id": request.request_id,
                "status": runtime_state.status,
                "policy": runtime_state.policy.model_dump(mode="json"),
                "security": runtime_state.security.model_dump(mode="json"),
            },
            "runtime_events": [
                event.model_dump(mode="json") for event in runtime_state.events
            ],
            "runtime_step_count": len(runtime_state.steps),
        }
