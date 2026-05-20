from typing import Any

from app.model_gateway.gateway import ModelGateway
from app.policy.engine import PolicyEngine
from app.schemas.query import QueryRequest, QueryResponse
from app.services.evaluator import Evaluator
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.runtime import (
    RuntimeExecutor,
    RuntimeResultAdapter,
    RuntimeStateFactory,
)
from app.services.tool_registry import ToolRegistry, ToolResult
from app.services.trace_store import TraceStore
from app.services.graph.graph_builder import build_harness_graph, resume_approval_decision
from app.tools.broker import ToolBroker
from app.tools.manifest import build_default_tool_manifests


class AgentHarness:
    """LangGraph-backed harness with deterministic fallback behavior."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        trace_store: TraceStore | None = None,
        memory_store: MemoryStore | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        evaluator: Evaluator | None = None,
        llm_client: Any | None = None,
        model_gateway: ModelGateway | None = None,
        policy_engine: PolicyEngine | None = None,
        tool_broker: ToolBroker | None = None,
        graph: Any | None = None,
        services: Any | None = None,
        runtime_factory: RuntimeStateFactory | None = None,
        runtime_executor: RuntimeExecutor | None = None,
        runtime_adapter: RuntimeResultAdapter | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model_gateway = model_gateway or ModelGateway(deepseek_client=llm_client)
        self.tool_registry = tool_registry or ToolRegistry(llm_client=self.model_gateway)
        self.trace_store = trace_store or TraceStore()
        self.policy_engine = policy_engine or PolicyEngine()
        self.tool_broker = tool_broker or ToolBroker(
            tool_registry=self.tool_registry,
            manifests=build_default_tool_manifests(self.tool_registry),
            policy_engine=self.policy_engine,
            trace_store=self.trace_store,
        )
        self.memory_store = memory_store or MemoryStore()
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.evaluator = evaluator or Evaluator()
        self.runtime_adapter = runtime_adapter or RuntimeResultAdapter()
        self.runtime_factory = runtime_factory or RuntimeStateFactory()
        self.runtime_executor = runtime_executor or RuntimeExecutor(
            adapter=self.runtime_adapter
        )
        self.services = services or self._build_services()
        if self.llm_client is None and getattr(self.services, "llm_client", None) is not None:
            self.llm_client = self.services.llm_client
        if (
            model_gateway is None
            and getattr(self.services, "model_gateway", None) is None
            and self.llm_client is not None
        ):
            self.model_gateway = ModelGateway(deepseek_client=self.llm_client)
        if not hasattr(self.services, "model_gateway"):
            self.services.model_gateway = self.model_gateway
        if not hasattr(self.services, "llm_client"):
            self.services.llm_client = self.llm_client
        if not hasattr(self.services, "policy_engine"):
            self.services.policy_engine = self.policy_engine
        if not hasattr(self.services, "tool_broker"):
            self.services.tool_broker = self.tool_broker
        if not hasattr(self.services, "approval_store") or self.services.approval_store is None:
            from app.services.approval_store import ApprovalStore

            self.services.approval_store = ApprovalStore()
        self._bind_trace_store_to_tools()
        self.graph = graph or build_harness_graph(self.services)

    async def answer(
        self,
        payload: QueryRequest,
        *,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QueryResponse:
        runtime_state = self.runtime_factory.from_query_request(
            payload,
            request_id=request_id,
            metadata=metadata,
        )
        runtime_result = await self.runtime_executor.execute(runtime_state, self.graph)
        return self.runtime_adapter.to_query_response(runtime_result)

    async def resume_approval(self, approval_record: Any) -> QueryResponse:
        final_state = await resume_approval_decision(self.services, approval_record)
        final_state.pop("response", None)
        runtime_result = self.runtime_adapter.from_harness_state(
            final_state,
            request_id=str(
                final_state.get("runtime_contract", {}).get("request_id")
                or final_state.get("runtime_request", {}).get("request_id")
                or approval_record.approval_id
            ),
            status=(
                "waiting_for_approval"
                if final_state.get("status") == "pending_approval"
                else "succeeded"
            ),
        )
        return self.runtime_adapter.to_query_response(runtime_result)

    def _build_services(self):
        from types import SimpleNamespace

        services = SimpleNamespace(
            tool_registry=self.tool_registry,
            trace_store=self.trace_store,
            memory_store=self.memory_store,
            sandbox_executor=self.sandbox_executor,
            evaluator=self.evaluator,
            llm_client=self.llm_client,
            model_gateway=self.model_gateway,
            policy_engine=self.policy_engine,
            tool_broker=self.tool_broker,
            approval_store=getattr(self.services, "approval_store", None)
            if hasattr(self, "services")
            else None,
            warnings=[],
        )
        services.agent_harness = self
        return services

    def _bind_trace_store_to_tools(self) -> None:
        trace_store = getattr(self.services, "trace_store", self.trace_store)
        registry = getattr(self.services, "tool_registry", self.tool_registry)
        get_tool = getattr(registry, "get", None)
        if not callable(get_tool):
            return
        try:
            manual_lookup = get_tool("manual_lookup")
        except Exception:
            return
        try:
            setattr(manual_lookup, "trace_store", trace_store)
            retriever = getattr(manual_lookup, "retriever", None)
            if retriever is not None:
                setattr(retriever, "trace_store", trace_store)
        except Exception:
            return

    async def answer_stream(
        self,
        payload: QueryRequest,
        *,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Stream intermediate states as Server-Sent Events.

        Yields StreamEvent dicts at key pipeline stages. Falls back to running
        the graph synchronously and yielding only the final event if LangGraph
        streaming is unavailable.
        """
        from app.schemas.streaming import StreamEvent, StreamEventType

        runtime_state = self.runtime_factory.from_query_request(
            payload,
            request_id=request_id,
            metadata=metadata,
        )
        session_id = runtime_state.request.session_id
        initial_state = runtime_state.harness_state

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
            runtime_result = await self.runtime_executor.execute(runtime_state, self.graph)
            response = self.runtime_adapter.to_query_response(runtime_result)
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
        runtime_result = self.runtime_adapter.from_harness_state(
            final_state,
            request_id=str(
                final_state.get("runtime_contract", {}).get("request_id")
                or final_state.get("runtime_request", {}).get("request_id")
                or "legacy"
            ),
        )
        return self.runtime_adapter.to_query_response(runtime_result)

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
