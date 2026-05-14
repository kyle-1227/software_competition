from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, Request

from app.services.agent_harness_lc import AgentHarness
from app.services.evaluator import Evaluator
from app.services.llm.deepseek_client import DeepSeekLLMClient
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.trace_store import TraceStore
from app.services.tool_registry import ToolRegistry


def _build_services() -> SimpleNamespace:
    trace_store = TraceStore()
    memory_store = MemoryStore()
    sandbox_executor = SandboxExecutor()
    evaluator = Evaluator()
    llm_client = DeepSeekLLMClient()
    tool_registry = ToolRegistry(llm_client=llm_client)
    services = SimpleNamespace(
        trace_store=trace_store,
        memory_store=memory_store,
        sandbox_executor=sandbox_executor,
        tool_registry=tool_registry,
        evaluator=evaluator,
        llm_client=llm_client,
        warnings=[],
    )
    services.agent_harness = AgentHarness(
        tool_registry=tool_registry,
        trace_store=trace_store,
        memory_store=memory_store,
        sandbox_executor=sandbox_executor,
        evaluator=evaluator,
        llm_client=llm_client,
        services=services,
    )
    return services


def _attach_services(app, services: SimpleNamespace) -> None:
    app.state.services = services
    app.state.agent_harness = services.agent_harness
    app.state.trace_store = services.trace_store
    app.state.memory_store = services.memory_store
    app.state.sandbox_executor = services.sandbox_executor
    app.state.tool_registry = services.tool_registry
    app.state.evaluator = services.evaluator
    app.state.llm_client = services.llm_client
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


def get_memory_store(request: Request) -> MemoryStore:
    return _get_services(request).memory_store


def get_sandbox_executor(request: Request) -> SandboxExecutor:
    return _get_services(request).sandbox_executor


def get_tool_registry(request: Request) -> ToolRegistry:
    return _get_services(request).tool_registry


def get_llm_client(request: Request) -> DeepSeekLLMClient:
    return _get_services(request).llm_client
