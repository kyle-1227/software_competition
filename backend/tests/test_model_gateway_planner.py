from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dependencies import _build_services
from app.model_gateway.gateway import ModelGateway
from app.planning.planner import Planner
from app.planning.task_plan import TaskPlan
from app.services.graph.graph_builder import _build_new_nodes
from app.services.orchestrator import Orchestrator


@pytest.mark.anyio
async def test_model_gateway_delegates_to_deepseek_compatible_client() -> None:
    client = _FakeClient(text='{"ok": true}')
    gateway = ModelGateway(deepseek_client=client)

    response = await gateway.generate_json("prompt", {"question": "x"}, task="planning")

    assert response.text == '{"ok": true}'
    assert response.provider == "deepseek"
    assert response.task == "planning"
    assert client.calls == [("json", "prompt", {"question": "x"})]


@pytest.mark.anyio
async def test_planner_outputs_task_plan_without_provider() -> None:
    plan = await Planner().plan("python script to inspect a maintenance log")

    assert isinstance(plan, TaskPlan)
    assert "ai_coding" in plan.workers
    assert "ai_coding" in plan.allowed_tools
    assert plan.risk_level in {"low", "medium", "high"}


@pytest.mark.anyio
async def test_orchestrator_uses_model_gateway_task_plan_fields() -> None:
    client = _FakeClient(
        text=(
            '{"intent":"sop_guidance","workers":["sop_guidance"],'
            '"risk_level":"medium","priority":"safety_first",'
            '"reasoning":"planned","allowed_tools":["manual_lookup"]}'
        )
    )
    gateway = ModelGateway(deepseek_client=client)

    decision = await Orchestrator(model_gateway=gateway).classify_and_plan("maintenance SOP")

    assert decision.intent == "sop_guidance"
    assert decision.task_plan is not None
    assert decision.risk_level == "medium"
    assert decision.allowed_tools == ["manual_lookup"]


@pytest.mark.anyio
async def test_orchestrator_state_contains_task_plan_risk_and_allowed_tools() -> None:
    services = SimpleNamespace(warnings=[], model_gateway=ModelGateway())
    nodes = _build_new_nodes(services)

    update = await nodes["orchestrator_node"](
        {
            "question": "python script",
            "device_name": None,
            "memory": [],
            "trace_id": None,
            "evidence": [],
            "tool_calls": [],
            "warnings": [],
            "degradation_events": [],
        }
    )

    assert isinstance(update["task_plan"], dict)
    assert update["risk_level"] == "medium"
    assert "ai_coding" in update["allowed_tools"]


def test_dependencies_inject_model_gateway() -> None:
    services = _build_services()

    assert isinstance(services.model_gateway, ModelGateway)
    assert services.agent_harness.model_gateway is services.model_gateway


def test_only_dependencies_constructs_deepseek_client_directly() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    matches: list[str] = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "DeepSeekLLMClient()" in text:
            matches.append(path.relative_to(app_dir).as_posix())

    assert matches == ["dependencies.py"]


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake-model"
        self.usage = {"input_tokens": 1}
        self.warnings: list[str] = []


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str, dict]] = []

    async def generate_json(self, prompt: str, context: dict | None = None):
        self.calls.append(("json", prompt, context or {}))
        return _FakeResponse(self.text)

    async def generate_text(self, prompt: str, context: dict | None = None):
        self.calls.append(("text", prompt, context or {}))
        return _FakeResponse(self.text)
