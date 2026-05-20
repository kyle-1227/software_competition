from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agent_harness_lc import AgentHarness
from app.services.agent_loop.controller import approval_scope_hash
from app.services.approval_store import ApprovalStore
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.tool_registry import ToolRegistry
from app.services.trace_store import TraceStore


@pytest.mark.anyio
async def test_approved_resume_injects_approval_scope_and_completes(tmp_path) -> None:
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    snapshot = {
        "question": "ECU replacement guidance",
        "device_name": "motor",
        "session_id": "session-1",
        "trace_id": "trace-1",
        "risk_level": "high",
        "answer": "",
        "plan": [],
        "evidence": [{"evidence_id": "ev-1", "source": "manual", "snippet": "Disconnect power first."}],
        "tool_calls": [],
        "sop": [],
        "warnings": [],
        "degradation_events": [],
        "loop_decision_count": 0,
        "approval_reason": "high risk",
    }
    scope = approval_scope_hash(snapshot)
    pending = store.create(
        reason="high risk",
        risk_level="high",
        trace_id="trace-1",
        session_id="session-1",
        approval_scope_hash=scope,
        state_snapshot={**snapshot, "approval_scope_hash": scope},
    )
    approved = store.decide(pending.approval_id, decision="approved")
    services = SimpleNamespace(
        tool_registry=ToolRegistry(),
        trace_store=TraceStore(storage_path=tmp_path / "traces"),
        memory_store=MemoryStore(),
        sandbox_executor=SandboxExecutor(),
        evaluator=_Evaluator(),
        llm_client=_LLM(),
        approval_store=store,
        warnings=[],
    )
    harness = AgentHarness(services=services)

    response = await harness.resume_approval(approved)

    assert response.status == "completed"
    assert response.approval is not None
    assert response.approval.approval_id == approved.approval_id
    assert response.answer
    assert response.evaluation is not None
    assert response.evaluation.confidence >= 0.9


class _LLM:
    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        del prompt, context
        return SimpleNamespace(text="Disconnect power first per ev-1.", model="test-model", usage={}, warnings=[])

    async def generate_json(self, prompt: str, context: dict[str, Any] | None = None):
        del prompt, context
        return SimpleNamespace(
            text='{"is_safe": true, "is_compliant": true, "confidence": 0.95, "issues": []}',
            model="test-model",
            usage={},
            warnings=[],
        )


class _Evaluator:
    async def evaluate(self, answer, evidence, tool_calls, sop):
        from app.schemas.query import EvaluationResult

        del answer, evidence, tool_calls, sop
        return EvaluationResult(is_safe=True, is_compliant=True, confidence=0.95)
