from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, Request

from app.model_gateway.gateway import ModelGateway
from app.policy.engine import PolicyEngine
from app.services.agent_harness_lc import AgentHarness
from app.services.approval_store import ApprovalStore
from app.services.evaluator import Evaluator
from app.services.llm.deepseek_client import DeepSeekLLMClient
from app.db.session import database_url
from app.knowledge.repository import KnowledgeRepository
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.trace_store import TraceStore
from app.services.tool_registry import ToolRegistry
from app.tools.broker import ToolBroker
from app.tools.manifest import build_default_tool_manifests


def _build_services() -> SimpleNamespace:
    trace_store = TraceStore()
    memory_store = MemoryStore()
    sandbox_executor = SandboxExecutor()
    evaluator = Evaluator()
    llm_client = DeepSeekLLMClient()
    model_gateway = ModelGateway(deepseek_client=llm_client)
    tool_registry = ToolRegistry(llm_client=llm_client)
    policy_engine = PolicyEngine()
    tool_broker = ToolBroker(
        tool_registry=tool_registry,
        manifests=build_default_tool_manifests(tool_registry),
        policy_engine=policy_engine,
        trace_store=trace_store,
    )
    db_url = database_url()
    knowledge_repository = KnowledgeRepository(db_url) if db_url else None
    approval_store = ApprovalStore()
    services = SimpleNamespace(
        trace_store=trace_store,
        knowledge_repository=knowledge_repository,
        memory_store=memory_store,
        sandbox_executor=sandbox_executor,
        tool_registry=tool_registry,
        policy_engine=policy_engine,
        tool_broker=tool_broker,
        approval_store=approval_store,
        evaluator=evaluator,
        llm_client=llm_client,
        model_gateway=model_gateway,
        warnings=[],
    )
    services.agent_harness = AgentHarness(
        tool_registry=tool_registry,
        trace_store=trace_store,
        memory_store=memory_store,
        sandbox_executor=sandbox_executor,
        evaluator=evaluator,
        llm_client=llm_client,
        model_gateway=model_gateway,
        policy_engine=policy_engine,
        tool_broker=tool_broker,
        services=services,
    )
    return services


def _attach_services(app, services: SimpleNamespace) -> None:
    app.state.services = services
    app.state.agent_harness = services.agent_harness
    app.state.trace_store = services.trace_store
    app.state.knowledge_repository = services.knowledge_repository
    app.state.memory_store = services.memory_store
    app.state.sandbox_executor = services.sandbox_executor
    app.state.tool_registry = services.tool_registry
    app.state.policy_engine = services.policy_engine
    app.state.tool_broker = services.tool_broker
    app.state.approval_store = services.approval_store
    app.state.evaluator = services.evaluator
    app.state.llm_client = services.llm_client
    app.state.model_gateway = services.model_gateway
    app.state.warnings = services.warnings


@asynccontextmanager
async def lifespan(app) -> AsyncIterator[None]:
    services = _build_services()
    _attach_services(app, services)
    yield


def _get_services(request: Request) -> SimpleNamespace:
    services = getattr(request.app.state, "services", None)
    if services is None:
        services = _build_services()
        _attach_services(request.app, services)
    return services


def get_agent_harness(request: Request) -> AgentHarness:
    return _get_services(request).agent_harness


def get_trace_store(request: Request) -> TraceStore:
    return _get_services(request).trace_store


def get_knowledge_repository(request: Request) -> KnowledgeRepository | None:
    return _get_services(request).knowledge_repository


def get_memory_store(request: Request) -> MemoryStore:
    return _get_services(request).memory_store


def get_sandbox_executor(request: Request) -> SandboxExecutor:
    return _get_services(request).sandbox_executor


def get_tool_registry(request: Request) -> ToolRegistry:
    return _get_services(request).tool_registry


def get_tool_broker(request: Request) -> ToolBroker:
    return _get_services(request).tool_broker


def get_approval_store(request: Request) -> ApprovalStore:
    return _get_services(request).approval_store


def get_llm_client(request: Request) -> DeepSeekLLMClient:
    return _get_services(request).llm_client


def get_model_gateway(request: Request) -> ModelGateway:
    return _get_services(request).model_gateway
