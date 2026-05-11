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
