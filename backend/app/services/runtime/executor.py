from __future__ import annotations

import asyncio
from typing import Any

from app.services.runtime.contracts import RuntimeEvent, RuntimeResult, RuntimeState, utc_now
from app.services.runtime.result_adapter import RuntimeResultAdapter
from app.services.tracing.serializers import redact_trace_text


class RuntimeExecutor:
    """Runs a RuntimeState through the LangGraph-compatible harness boundary."""

    def __init__(self, adapter: RuntimeResultAdapter | None = None) -> None:
        self.adapter = adapter or RuntimeResultAdapter()

    async def execute(self, runtime_state: RuntimeState, graph: Any) -> RuntimeResult:
        runtime_state.status = "running"
        runtime_state.started_at = utc_now()
        runtime_state.events.append(
            RuntimeEvent(type="runtime.started", message="Runtime execution started")
        )
        harness_state = {
            **runtime_state.harness_state,
            "runtime_contract": {
                **runtime_state.harness_state.get("runtime_contract", {}),
                "status": runtime_state.status,
            },
            "runtime_events": [
                event.model_dump(mode="json") for event in runtime_state.events
            ],
        }

        try:
            final_state = await self._invoke_graph(
                graph,
                harness_state,
                runtime_state.request.session_id,
                runtime_state.policy.timeout_seconds,
            )
        except TimeoutError as exc:
            runtime_state.status = "timeout"
            return self._failed_result(runtime_state, exc)
        except Exception as exc:
            runtime_state.status = "failed"
            return self._failed_result(runtime_state, exc)

        runtime_state.status = "succeeded"
        runtime_state.ended_at = utc_now()
        runtime_state.events.append(
            RuntimeEvent(type="runtime.completed", message="Runtime execution completed")
        )
        final_state["runtime_contract"] = {
            **final_state.get("runtime_contract", {}),
            "request_id": runtime_state.request.request_id,
            "status": runtime_state.status,
        }
        final_state["runtime_events"] = [
            event.model_dump(mode="json") for event in runtime_state.events
        ]
        return self.adapter.from_harness_state(
            final_state,
            request_id=runtime_state.request.request_id,
            status=runtime_state.status,
            events=runtime_state.events,
            started_at=runtime_state.started_at,
        )

    async def _invoke_graph(
        self,
        graph: Any,
        initial_state: dict[str, Any],
        session_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        async def invoke() -> dict[str, Any]:
            try:
                return await graph.ainvoke(
                    initial_state,
                    config={"configurable": {"thread_id": session_id}},
                )
            except TypeError as exc:
                message = str(exc)
                if (
                    "multiple values for argument 'config'" in message
                    or "unexpected keyword argument 'config'" in message
                ):
                    return await graph.ainvoke(initial_state)
                raise

        if timeout_seconds <= 0:
            return await invoke()
        return await asyncio.wait_for(invoke(), timeout=timeout_seconds)

    def _failed_result(
        self,
        runtime_state: RuntimeState,
        exc: Exception,
    ) -> RuntimeResult:
        runtime_state.ended_at = utc_now()
        clean_error = redact_trace_text(str(exc) or exc.__class__.__name__)[:500]
        runtime_state.events.append(
            RuntimeEvent(
                type=f"runtime.{runtime_state.status}",
                message="Runtime execution failed",
                data={"error": clean_error, "error_type": exc.__class__.__name__},
            )
        )
        fallback_state = {
            "answer": "服务暂时无法完成此请求，请稍后重试。",
            "plan": [],
            "evidence": [],
            "tool_calls": [],
            "evaluation": None,
            "trace_id": None,
            "sop": [],
            "memory": [],
            "ai_coding": None,
            "llm_usage": None,
            "llm_model": None,
            "errors": [clean_error],
            "warnings": [],
            "runtime_contract": {
                "request_id": runtime_state.request.request_id,
                "status": runtime_state.status,
            },
            "runtime_events": [
                event.model_dump(mode="json") for event in runtime_state.events
            ],
        }
        return self.adapter.from_harness_state(
            fallback_state,
            request_id=runtime_state.request.request_id,
            status=runtime_state.status,
            events=runtime_state.events,
            started_at=runtime_state.started_at,
        )
