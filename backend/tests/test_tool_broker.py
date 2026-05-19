from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.policy.engine import PolicyEngine
from app.services.tool_registry import BaseTool, ToolRegistry, ToolResult
from app.services.tools.ai_coding import AICodingTool
from app.services.workers.fault_triage import FaultTriageWorker
from app.tools.broker import ToolBroker
from app.tools.manifest import ToolManifest


@pytest.mark.anyio
async def test_tool_broker_executes_after_governance() -> None:
    registry = ToolRegistry(register_defaults=False)
    registry.register(_EchoTool())
    broker = ToolBroker(
        tool_registry=registry,
        manifests={
            "echo": ToolManifest(
                name="echo",
                parameters_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                allowed_callers=["test"],
            )
        },
        policy_engine=PolicyEngine(),
    )

    result = await broker.execute("echo", {"text": "ok"}, caller="test", risk_level="low")

    assert result.success is True
    assert result.data == {"text": "ok"}
    assert result.metadata["brokered"] is True


@pytest.mark.anyio
async def test_tool_broker_rejects_schema_and_policy_violations() -> None:
    registry = ToolRegistry(register_defaults=False)
    registry.register(_EchoTool())
    broker = ToolBroker(
        tool_registry=registry,
        manifests={
            "echo": ToolManifest(
                name="echo",
                parameters_schema={"type": "object", "required": ["text"]},
                allowed_callers=["allowed"],
                max_risk_level="low",
            )
        },
        policy_engine=PolicyEngine(),
    )

    missing = await broker.execute("echo", {}, caller="allowed", risk_level="low")
    blocked = await broker.execute("echo", {"text": "ok"}, caller="other", risk_level="low")
    risky = await broker.execute("echo", {"text": "ok"}, caller="allowed", risk_level="high")

    assert missing.success is False
    assert "missing required" in str(missing.error)
    assert blocked.success is False
    assert "not allowed" in str(blocked.error)
    assert risky.success is False
    assert "risk level" in str(risky.error)


@pytest.mark.anyio
async def test_manual_lookup_worker_uses_tool_broker() -> None:
    broker = _RecordingBroker()
    services = SimpleNamespace(
        tool_broker=broker,
        tool_registry=object(),
        trace_store=None,
    )

    result = await FaultTriageWorker().execute(
        {
            "question": "engine cannot start",
            "intent": "fault_triage",
            "risk_level": "low",
            "trace_id": "trace-1",
        },
        services,
    )

    assert broker.calls
    assert broker.calls[0]["name"] == "manual_lookup"
    assert broker.calls[0]["caller"] == "fault_triage"
    assert broker.calls[0]["risk_level"] == "low"
    assert result["evidence"][0]["metadata"]["chunk_id"] == "chunk-1"


@pytest.mark.anyio
async def test_ai_coding_tool_does_not_decide_execution_allowed() -> None:
    result = await AICodingTool(llm_client=None).run(
        {"task": "print a diagnostic message", "language": "python"}
    )

    assert result.success is True
    assert isinstance(result.data, dict)
    assert "script" in result.data
    assert "execution_allowed" not in result.data


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echo text"
    parameters_schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def run(self, payload: dict) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, data={"text": payload["text"]})


class _RecordingBroker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(
        self,
        name: str,
        payload: dict,
        *,
        caller: str = "unknown",
        risk_level: str = "unknown",
        trace_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolResult:
        self.calls.append(
            {
                "name": name,
                "payload": payload,
                "caller": caller,
                "risk_level": risk_level,
                "trace_id": trace_id,
                "run_id": run_id,
            }
        )
        return ToolResult(
            tool_name=name,
            success=True,
            data=[
                {
                    "source": "manual",
                    "page": 1,
                    "snippet": "starter check",
                    "metadata": {"chunk_id": "chunk-1"},
                }
            ],
        )
