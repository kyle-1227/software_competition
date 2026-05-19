from __future__ import annotations

from typing import Any

from app.schemas.query import QueryResponse
from app.services.runtime.contracts import RuntimeResult
from app.services.tracing.serializers import redact_trace_text, sanitize_trace_dict


class RuntimeResultAdapter:
    """Converts internal runtime output back to the stable public API response."""

    def from_harness_state(
        self,
        final_state: dict[str, Any],
        *,
        request_id: str,
        status: str = "succeeded",
        events: list[Any] | None = None,
        started_at: Any = None,
    ) -> RuntimeResult:
        response = final_state.get("response")
        if isinstance(response, QueryResponse):
            response_dict = response.model_dump(mode="json")
        elif isinstance(response, dict):
            response_dict = QueryResponse(**response).model_dump(mode="json")
        else:
            response_dict = QueryResponse(
                answer=str(final_state.get("answer", "")),
                plan=final_state.get("plan", []),
                evidence=final_state.get("evidence", []),
                tool_calls=final_state.get("tool_calls", []),
                evaluation=final_state.get("evaluation"),
                trace_id=final_state.get("trace_id"),
                sop=final_state.get("sop", []),
                memory=final_state.get("memory", []),
                ai_coding=final_state.get("ai_coding"),
                llm_usage=final_state.get("llm_usage"),
                llm_model=final_state.get("llm_model"),
            ).model_dump(mode="json")

        return RuntimeResult(
            request_id=request_id,
            status=status,  # type: ignore[arg-type]
            trace_id=response_dict.get("trace_id") or final_state.get("trace_id"),
            response=response_dict,
            errors=self._clean_messages(final_state.get("errors", [])),
            warnings=self._clean_messages(final_state.get("warnings", [])),
            events=events or [],
            harness_state=sanitize_trace_dict(
                {
                    "trace_id": final_state.get("trace_id"),
                    "plan": final_state.get("plan", []),
                    "tool_calls": final_state.get("tool_calls", []),
                    "degradation_events": final_state.get("degradation_events", []),
                    "runtime_contract": final_state.get("runtime_contract", {}),
                    "runtime_events": final_state.get("runtime_events", []),
                }
            ),
            started_at=started_at,
        )

    def to_query_response(self, result: RuntimeResult) -> QueryResponse:
        return QueryResponse(**result.response)

    @staticmethod
    def _clean_messages(messages: Any) -> list[str]:
        if not isinstance(messages, list):
            return []
        return [redact_trace_text(str(item))[:500] for item in messages]
