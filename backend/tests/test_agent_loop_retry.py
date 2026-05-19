from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.query import SandboxResult
from app.services.agent_loop.retry import (
    execute_sandbox_with_retry,
    execute_tool_with_retry,
)
from app.services.tool_registry import ToolResult
from app.services.workers.ai_coding import AICodingWorker
from app.core.config import settings


@pytest.mark.anyio
async def test_tool_retry_success_after_transient_failures() -> None:
    registry = _FlakyRegistry(
        failures=3,
        success=ToolResult(tool_name="manual_lookup", success=True, data=[]),
    )

    result = await execute_tool_with_retry(
        registry,
        "manual_lookup",
        {"question": "q"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
    )

    assert result.success is True
    assert result.degraded is False
    assert result.attempts == 4
    assert len(result.tool_calls) == 4


@pytest.mark.anyio
async def test_tool_retry_degrades_after_5_failures() -> None:
    registry = _AlwaysFailRegistry()

    result = await execute_tool_with_retry(
        registry,
        "unknown_tool",
        {"question": "q"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
    )

    assert result.success is False
    assert result.degraded is True
    assert result.attempts == 5
    assert result.degradation_events[0]["type"] == "tool_degraded"
    assert result.tool_calls[-1]["status"] == "degraded"


@pytest.mark.anyio
async def test_manual_lookup_degrades_to_placeholder_after_5_failures() -> None:
    registry = _AlwaysFailRegistry()

    result = await execute_tool_with_retry(
        registry,
        "manual_lookup",
        {"question": "spark plug gap"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
    )

    assert result.success is True
    assert result.degraded is True
    evidence = result.result.data
    assert isinstance(evidence, list)
    assert evidence[0]["metadata"]["retriever"] == "manual_lookup-degraded"
    assert evidence[0]["metadata"]["retry_attempts"] == 5


@pytest.mark.anyio
async def test_manual_lookup_degraded_placeholder_suppressed_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    registry = _AlwaysFailRegistry()

    result = await execute_tool_with_retry(
        registry,
        "manual_lookup",
        {"question": "spark plug gap"},
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
    )

    assert result.success is True
    assert result.degraded is True
    assert result.result is not None
    assert result.result.data == []
    assert result.result.metadata["placeholder_suppressed"] is True


@pytest.mark.anyio
async def test_ai_coding_degrades_without_sandbox_after_5_failures() -> None:
    worker = AICodingWorker()
    sandbox = _CountingSandbox()
    services = SimpleNamespace(
        tool_registry=_AlwaysFailRegistry(),
        sandbox_executor=sandbox,
    )

    result = await worker.execute({"question": "write a python diagnostic script"}, services)

    assert result["ai_coding"]["degraded"] is True
    assert result["requires_human_approval"] is True
    assert sandbox.calls == 0
    assert result["retry_attempts"] == 5


@pytest.mark.anyio
async def test_sandbox_retries_5_times_then_blocks() -> None:
    sandbox = _FailingSandbox()

    result = await execute_sandbox_with_retry(
        sandbox,
        "print('diagnose')",
        "python",
        max_retries=5,
        backoff_ms=[0, 0, 0, 0, 0],
    )

    assert sandbox.calls == 5
    assert result.success is False
    assert result.degraded is True
    assert result.attempts == 5
    assert result.result.data["allowed"] is False
    assert result.degradation_events[0]["type"] == "sandbox_degraded"


class _FlakyRegistry:
    def __init__(self, failures: int, success: ToolResult) -> None:
        self.failures = failures
        self.success = success
        self.calls = 0

    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        self.calls += 1
        if self.calls <= self.failures:
            return ToolResult(tool_name=name, success=False, error="transient")
        return self.success


class _AlwaysFailRegistry:
    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        del payload
        return ToolResult(tool_name=name, success=False, error="boom")


class _CountingSandbox:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, script: str, language: str) -> SandboxResult:
        del script, language
        self.calls += 1
        return SandboxResult(language="python", allowed=True, return_code=0)


class _FailingSandbox:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, script: str, language: str) -> SandboxResult:
        del script, language
        self.calls += 1
        return SandboxResult(language="python", allowed=False, error="blocked")
