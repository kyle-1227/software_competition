from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.graph.graph_builder import _build_shared_nodes
from app.services.memory_store import MemoryStore
from app.services.trace_store import TraceStore


@pytest.mark.anyio
async def test_pending_approval_is_not_written_to_long_term_memory(tmp_path) -> None:
    memory = MemoryStore()
    nodes = _build_shared_nodes(
        SimpleNamespace(memory_store=memory, trace_store=TraceStore(storage_path=tmp_path))
    )

    await nodes["memory_save_node"](
        {
            "session_id": "session-1",
            "status": "pending_approval",
            "verification_passed": True,
            "question": "q",
            "answer": "waiting",
            "evidence": [],
            "tool_calls": [],
        }
    )

    assert memory.get_history("session-1") == []


@pytest.mark.anyio
async def test_only_verified_completed_answer_is_written_to_memory(tmp_path) -> None:
    memory = MemoryStore()
    nodes = _build_shared_nodes(
        SimpleNamespace(memory_store=memory, trace_store=TraceStore(storage_path=tmp_path))
    )

    await nodes["memory_save_node"](
        {
            "session_id": "session-1",
            "status": "completed",
            "verification_passed": True,
            "verification_skipped_reason": None,
            "question": "q",
            "answer": "final",
            "evidence": [],
            "tool_calls": [],
            "evaluation": None,
            "sandbox_result": None,
        }
    )

    assert memory.get_history("session-1")[0]["answer"] == "final"
