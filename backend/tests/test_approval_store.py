from __future__ import annotations

import pytest

from app.services.approval_store import ApprovalStore


def test_approval_store_replays_append_only_latest_state(tmp_path) -> None:
    path = tmp_path / "approvals.jsonl"
    store = ApprovalStore(path)
    record = store.create(
        reason="risk",
        risk_level="high",
        trace_id="trace-1",
        session_id="session-1",
        approval_scope_hash="scope-1",
        state_snapshot={"question": "q", "secret": "api_key=real-api-key"},
    )

    decided = store.decide(record.approval_id, decision="approved", reviewer="human")
    replayed = ApprovalStore(path).get(record.approval_id)

    assert decided.status == "approved"
    assert replayed is not None
    assert replayed.status == "approved"
    assert replayed.reviewer == "human"


def test_approval_store_rejects_duplicate_decisions_atomically(tmp_path) -> None:
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    record = store.create(
        reason="risk",
        risk_level="high",
        trace_id="trace-1",
        session_id="session-1",
        approval_scope_hash="scope-1",
        state_snapshot={"question": "q"},
    )

    store.decide(record.approval_id, decision="rejected")

    with pytest.raises(ValueError):
        store.decide(record.approval_id, decision="approved")
