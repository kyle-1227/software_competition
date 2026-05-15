from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.orchestrator import OrchestratorDecision
from app.services.graph.graph_builder import _build_new_nodes
from app.services.workers.dispatcher import WorkerDispatcher


@pytest.mark.anyio
async def test_worker_dispatcher_preserves_evidence_and_tool_calls() -> None:
    result = {
        "evidence": [_evidence("chunk-1", "manual", 3, "spark plug")],
        "tool_calls": [_tool_call("manual_lookup", {"question": "q"})],
        "worker_outputs": [{"worker": "fault_triage", "evidence_count": 1}],
    }
    dispatcher = WorkerDispatcher({"fault_triage": _FakeWorker("fault_triage", result)})

    worker_results = await dispatcher.dispatch(
        OrchestratorDecision(intent="fault_triage", workers=["fault_triage"]),
        {"question": "q"},
        SimpleNamespace(),
    )

    assert worker_results == [result]
    assert worker_results[0]["evidence"][0]["metadata"]["chunk_id"] == "chunk-1"
    assert worker_results[0]["tool_calls"][0]["tool_name"] == "manual_lookup"


@pytest.mark.anyio
async def test_worker_executor_merges_ai_coding_and_sandbox_result() -> None:
    ai_coding = {"language": "python", "script": "print(1)"}
    sandbox_result = {"language": "python", "allowed": True, "return_code": 0}
    services = _services_with_dispatch_results(
        [
            {
                "ai_coding": ai_coding,
                "sandbox_result": sandbox_result,
                "tool_calls": [_tool_call("ai_coding", {"task": "q"})],
                "worker_outputs": [{"worker": "ai_coding", "sandbox_allowed": True}],
            }
        ]
    )
    nodes = _build_new_nodes(services)

    update = await nodes["worker_executor_node"](
        {
            "question": "write code",
            "_orchestrator_decision": OrchestratorDecision(
                intent="ai_coding",
                workers=["ai_coding"],
            ),
            "evidence": [],
            "tool_calls": [],
            "sop": [],
            "warnings": [],
        }
    )

    assert update["ai_coding"] == ai_coding
    assert update["sandbox_result"] == sandbox_result
    assert update["worker_outputs"] == [{"worker": "ai_coding", "sandbox_allowed": True}]
    assert update["tool_calls"][0]["tool_name"] == "ai_coding"


@pytest.mark.anyio
async def test_worker_executor_deduplicates_evidence() -> None:
    duplicate_by_chunk = _evidence("chunk-1", "manual", 3, "same")
    duplicate_by_source = _evidence(None, "manual", 4, "same snippet")
    services = _services_with_dispatch_results(
        [
            {
                "evidence": [
                    duplicate_by_chunk,
                    duplicate_by_chunk,
                    duplicate_by_source,
                    duplicate_by_source,
                ],
                "tool_calls": [
                    _tool_call("manual_lookup", {"question": "q"}),
                    _tool_call("manual_lookup", {"question": "q"}),
                ],
                "sop": ["step 1", "step 1", "step 2"],
                "warnings": ["warn", "warn"],
                "worker_outputs": [{"worker": "fault_triage"}],
            }
        ]
    )
    nodes = _build_new_nodes(services)

    update = await nodes["worker_executor_node"](
        {
            "question": "q",
            "_orchestrator_decision": OrchestratorDecision(
                intent="fault_triage",
                workers=["fault_triage"],
            ),
            "evidence": [],
            "tool_calls": [],
            "sop": [],
            "warnings": [],
        }
    )

    assert len(update["evidence"]) == 2
    assert len(update["tool_calls"]) == 1
    assert update["sop"] == ["step 1", "step 2"]
    assert update["warnings"] == ["warn"]


class _FakeWorker:
    def __init__(self, name: str, result: dict[str, Any]) -> None:
        self.name = name
        self.result = result

    async def execute(self, state: dict[str, Any], services: Any) -> dict[str, Any]:
        del state, services
        return self.result


class _FakeDispatcher:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    async def dispatch(
        self,
        decision: OrchestratorDecision,
        state: dict[str, Any],
        services: Any,
    ) -> list[dict[str, Any]]:
        del decision, state, services
        return self.results


def _services_with_dispatch_results(results: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        orchestrator=object(),
        input_guardrail=object(),
        worker_dispatcher=_FakeDispatcher(results),
        llm_client=None,
        llm_evaluator=object(),
        evaluator_optimizer=object(),
        output_guardrail=object(),
        tool_registry=object(),
        trace_store=object(),
        memory_store=object(),
        sandbox_executor=object(),
        evaluator=object(),
        warnings=[],
    )


def _evidence(
    chunk_id: str | None,
    source: str,
    page: int,
    snippet: str,
) -> dict[str, Any]:
    metadata = {}
    if chunk_id:
        metadata["chunk_id"] = chunk_id
    return {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": 0.9,
        "metadata": metadata,
    }


def _tool_call(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "input": payload,
        "output": {},
        "status": "success",
    }
