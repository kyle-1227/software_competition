import os
import sys
from pathlib import Path
from typing import Any

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
# 允许从仓库根目录运行 pytest 时直接导入 app.*。
sys.path.insert(0, str(BACKEND_DIR))
# 单元测试默认离线运行，避免真实 .env 中的 DeepSeek key 触发外部调用。
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["SILICONFLOW_API_KEY"] = ""
if os.environ.get("RUN_POSTGRES_TESTS") != "true":
    os.environ["TRACE_BACKEND"] = "jsonl"
    os.environ["TRACE_DATABASE_URL"] = ""
    os.environ["DATABASE_URL"] = ""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _force_offline_llm_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_LIVE_LLM_TESTS", raising=False)


# ---------------------------------------------------------------------------
# Phase 0: reusable service fixtures
# ---------------------------------------------------------------------------


class FakeLLMResponse:
    """Configurable fake LLM response for tests."""

    def __init__(
        self,
        text: str = "安全提醒：请先停机断电。根据手册 P.3 检查火花塞间隙。",
        model: str | None = "test-model",
        usage: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.text = text
        self.model = model
        self.usage = usage or {}
        self.warnings = warnings or []


class RecordingLLMClient:
    """LLM client that returns pre-configured responses and records calls."""

    def __init__(
        self,
        response: FakeLLMResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeLLMResponse()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_text(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> FakeLLMResponse:
        self.calls.append({"method": "generate_text", "prompt": prompt, "context": context})
        if self.error is not None:
            raise self.error
        return self.response

    async def generate_json(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> FakeLLMResponse:
        self.calls.append({"method": "generate_json", "prompt": prompt, "context": context})
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def memory_store():
    from app.services.memory_store import MemoryStore

    return MemoryStore()


@pytest.fixture
def trace_store():
    from app.services.trace_store import TraceStore

    return TraceStore()


@pytest.fixture
def sandbox_executor():
    from app.services.sandbox import SandboxExecutor

    return SandboxExecutor()


@pytest.fixture
def evaluator():
    from app.services.evaluator import Evaluator

    return Evaluator()


@pytest.fixture
def empty_tool_registry():
    from app.services.tool_registry import ToolRegistry

    return ToolRegistry(register_defaults=False)


@pytest.fixture
def tool_registry():
    from app.services.tool_registry import ToolRegistry

    return ToolRegistry()


@pytest.fixture
def recording_llm_client():
    return RecordingLLMClient()


@pytest.fixture
def harness_services(
    memory_store,
    trace_store,
    tool_registry,
    sandbox_executor,
    evaluator,
    recording_llm_client,
):
    from types import SimpleNamespace

    svc = SimpleNamespace(
        tool_registry=tool_registry,
        trace_store=trace_store,
        memory_store=memory_store,
        sandbox_executor=sandbox_executor,
        evaluator=evaluator,
        llm_client=recording_llm_client,
        warnings=[],
    )
    return svc


@pytest.fixture
def agent_harness(harness_services):
    from app.services.agent_harness_lc import AgentHarness

    return AgentHarness(
        tool_registry=harness_services.tool_registry,
        trace_store=harness_services.trace_store,
        memory_store=harness_services.memory_store,
        sandbox_executor=harness_services.sandbox_executor,
        evaluator=harness_services.evaluator,
        llm_client=harness_services.llm_client,
        services=harness_services,
    )
