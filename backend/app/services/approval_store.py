from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.config import BACKEND_DIR, settings
from app.services.tracing.serializers import redact_trace_text

ApprovalStatus = Literal["pending", "approved", "rejected"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalRecord(BaseModel):
    approval_id: str
    status: ApprovalStatus = "pending"
    reason: str | None = None
    risk_level: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    approval_scope_hash: str
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None
    note: str | None = None
    created_at: str
    updated_at: str
    decided_at: str | None = None


class ApprovalStore:
    """Append-only JSONL approval log.

    Single-process assumption: the in-memory index and lock are authoritative
    for atomic status transitions inside one running API process. The JSONL
    file is replayed on startup to recover the latest state.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or settings.approval_store_path
        self.path = Path(configured)
        if not self.path.is_absolute():
            self.path = (BACKEND_DIR / self.path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: dict[str, ApprovalRecord] = {}
        self._replay()

    def create(
        self,
        *,
        reason: str | None,
        risk_level: str | None,
        trace_id: str | None,
        session_id: str | None,
        approval_scope_hash: str,
        state_snapshot: dict[str, Any],
    ) -> ApprovalRecord:
        now = _utc_now()
        record = ApprovalRecord(
            approval_id=str(uuid4()),
            status="pending",
            reason=reason,
            risk_level=risk_level,
            trace_id=trace_id,
            session_id=session_id,
            approval_scope_hash=approval_scope_hash,
            state_snapshot=_sanitize_snapshot(state_snapshot),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._records[record.approval_id] = record
            self._append({"event": "created", **record.model_dump(mode="json")})
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._records.get(approval_id)

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        records = list(self._records.values())
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def decide(
        self,
        approval_id: str,
        *,
        decision: Literal["approved", "rejected"],
        reviewer: str | None = None,
        note: str | None = None,
    ) -> ApprovalRecord:
        with self._lock:
            current = self._records.get(approval_id)
            if current is None:
                raise KeyError(f"Approval not found: {approval_id}")
            if current.status != "pending":
                raise ValueError(f"Approval already decided: {current.status}")
            updated = current.model_copy(
                update={
                    "status": decision,
                    "reviewer": reviewer,
                    "note": note,
                    "updated_at": _utc_now(),
                    "decided_at": _utc_now(),
                }
            )
            self._records[approval_id] = updated
            self._append({"event": "decided", **updated.model_dump(mode="json")})
            return updated

    def _append(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _replay(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    record = ApprovalRecord(**{
                        key: value
                        for key, value in event.items()
                        if key != "event"
                    })
                except Exception:
                    continue
                self._records[record.approval_id] = record


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
    "reasoning",
    "thinking",
    "chain_of_thought",
)


def _sanitize_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _sanitize_snapshot(item)
        return result
    if isinstance(value, list):
        return [_sanitize_snapshot(item) for item in value]
    if isinstance(value, str):
        return redact_trace_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_trace_text(str(value))
