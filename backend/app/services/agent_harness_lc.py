from typing import Any

from app.schemas.query import QueryRequest, QueryResponse
from app.services.evaluator import Evaluator
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.llm.deepseek_client import DeepSeekLLMClient
from app.services.tool_registry import ToolRegistry, ToolResult
from app.services.trace_store import TraceStore
from app.services.graph.graph_builder import build_harness_graph


class AgentHarness:
    """LangGraph-backed harness with deterministic fallback behavior."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        trace_store: TraceStore | None = None,
        memory_store: MemoryStore | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        evaluator: Evaluator | None = None,
        llm_client: DeepSeekLLMClient | None = None,
        graph: Any | None = None,
        services: Any | None = None,
    ) -> None:
        self.tool_registry = tool_registry or ToolRegistry()
        self.trace_store = trace_store or TraceStore()
        self.memory_store = memory_store or MemoryStore()
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.evaluator = evaluator or Evaluator()
        self.llm_client = llm_client or DeepSeekLLMClient()
        self.services = services or self._build_services()
        self.graph = graph or build_harness_graph(self.services)

    async def answer(self, payload: QueryRequest) -> QueryResponse:
        session_id = payload.session_id or payload.device_model or payload.device_name or "default"
        initial_state = {
            "question": payload.question,
            "device_name": payload.device_name,
            "device_model": payload.device_model,
            "session_id": session_id,
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
            "loop_decision_count": 0,
            "loop_history": [],
            "tool_retry_counts": {},
            "retrieval_retry_count": 0,
            "answer_regeneration_count": 0,
            "degradation_events": [],
            "requires_human_approval": False,
            "approval_reason": None,
            "clarification_question": None,
            "fail_safe_reason": None,
        }
        final_state = await self._invoke_graph(initial_state, session_id)
        response = final_state.get("response")
        if isinstance(response, QueryResponse):
            return response
        if isinstance(response, dict):
            return QueryResponse(**response)
        if isinstance(final_state, dict):
            return QueryResponse(
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
            )
        raise TypeError("AgentHarness graph did not return a valid response")

    def _build_services(self):
        from types import SimpleNamespace

        services = SimpleNamespace(
            tool_registry=self.tool_registry,
            trace_store=self.trace_store,
            memory_store=self.memory_store,
            sandbox_executor=self.sandbox_executor,
            evaluator=self.evaluator,
            llm_client=self.llm_client,
            warnings=[],
        )
        services.agent_harness = self
        return services

    async def answer_stream(self, payload: QueryRequest):
        """Stream intermediate states as Server-Sent Events.

        Yields StreamEvent dicts at key pipeline stages. Falls back to running
        the graph synchronously and yielding only the final event if LangGraph
        streaming is unavailable.
        """
        from app.schemas.streaming import StreamEvent, StreamEventType

        session_id = payload.session_id or payload.device_model or payload.device_name or "default"
        initial_state = {
            "question": payload.question,
            "device_name": payload.device_name,
            "device_model": payload.device_model,
            "session_id": session_id,
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
            "clarification_question": None,
            "fail_safe_reason": None,
        }

        # Try LangGraph streaming, fall back to sequential execution with manual yields
        try:
            async for event in self.graph.astream_events(
                initial_state,
                config={"configurable": {"thread_id": session_id}},
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_chain_start":
                    yield StreamEvent(
                        type=self._map_node_to_event_type(name, "start"),
                        data={"node": name},
                    ).model_dump(mode="json")
                elif kind == "on_chain_end":
                    yield StreamEvent(
                        type=self._map_node_to_event_type(name, "end"),
                        data={"node": name},
                    ).model_dump(mode="json")
        except Exception:
            # Fallback: run full graph and yield final
            final_state = await self._invoke_graph(initial_state, session_id)
            response = self._state_to_response(final_state)
            yield StreamEvent(
                type=StreamEventType.FINAL_RESPONSE,
                data=response.model_dump(mode="json"),
            ).model_dump(mode="json")

    @staticmethod
    def _map_node_to_event_type(node_name: str, phase: str):
        from app.schemas.streaming import StreamEventType
        if "guardrail" in node_name and phase == "end":
            return StreamEventType.GUARDRAIL_PASSED
        if "orchestrator" in node_name and phase == "end":
            return StreamEventType.INTENT_CLASSIFIED
        if "worker" in node_name:
            return StreamEventType.WORKER_COMPLETED if phase == "end" else StreamEventType.WORKER_STARTED
        if "draft" in node_name and phase == "start":
            return StreamEventType.ANSWER_GENERATING
        if "evaluator" in node_name and phase == "end":
            return StreamEventType.EVALUATION_COMPLETE
        if "finalize" in node_name and phase == "end":
            return StreamEventType.FINAL_RESPONSE
        return StreamEventType.EVIDENCE_RETRIEVED

    def _state_to_response(self, final_state: dict[str, Any]) -> QueryResponse:
        response = final_state.get("response")
        if isinstance(response, QueryResponse):
            return response
        if isinstance(response, dict):
            return QueryResponse(**response)
        if isinstance(final_state, dict):
            return QueryResponse(
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
            )
        raise TypeError("AgentHarness graph did not return a valid response")

    async def _invoke_graph(
        self, initial_state: dict[str, Any], session_id: str
    ) -> dict[str, Any]:
        try:
            return await self.graph.ainvoke(
                initial_state, config={"configurable": {"thread_id": session_id}}
            )
        except TypeError as exc:
            message = str(exc)
            if "multiple values for argument 'config'" in message or "unexpected keyword argument 'config'" in message:
                return await self.graph.ainvoke(initial_state)
            raise
