from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from typing import Any

from app.core.config import settings
from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, ToolCallItem
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan

logger = logging.getLogger(__name__)


class TraceStore:
    """Execution trace store with nested span model + flat legacy compat.

    New code uses start_trace_session / add_span / close_trace with the
    Trace/TraceSpan pydantic models. Legacy record_* methods are preserved
    as wrappers that emit into the root span.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._traces: dict[str, dict[str, object]] = {}  # legacy flat dicts
        self._trace_sessions: dict[str, Trace] = {}       # new nested spans
        self._storage_path = storage_path

    # ------------------------------------------------------------------
    # Legacy API (kept for backward compatibility)
    # ------------------------------------------------------------------

    def start_trace(self) -> str:
        """Legacy: returns trace_id string. Also creates a Trace session."""
        trace_id = str(uuid4())
        self._traces[trace_id] = {
            "plan": [],
            "tool_calls": [],
            "evidence": [],
            "answer": None,
            "evaluation": None,
            "memory": [],
            "sandbox_result": None,
        }
        root_span = TraceSpan(name="harness", kind=SpanKind.AGENT)
        self._trace_sessions[trace_id] = Trace(
            trace_id=trace_id,
            session_id="",
            question="",
            root_span=root_span,
        )
        return trace_id

    def record_plan(self, trace_id: str, plan: list[PlanStep]) -> None:
        self._ensure_trace(trace_id)["plan"] = plan

    def record_tool_call(self, trace_id: str, tool_call: ToolCallItem) -> None:
        trace = self._ensure_trace(trace_id)
        tool_calls = trace.setdefault("tool_calls", [])
        assert isinstance(tool_calls, list)
        tool_calls.append(tool_call)

    def record_evidence(self, trace_id: str, evidence: list[EvidenceItem]) -> None:
        self._ensure_trace(trace_id)["evidence"] = evidence

    def record_answer(self, trace_id: str, answer: str) -> None:
        self._ensure_trace(trace_id)["answer"] = answer

    def record_evaluation(
        self, trace_id: str, evaluation: EvaluationResult
    ) -> None:
        self._ensure_trace(trace_id)["evaluation"] = evaluation

    def record_memory(self, trace_id: str, memory: list[dict[str, Any]]) -> None:
        self._ensure_trace(trace_id)["memory"] = memory

    def record_sandbox_result(
        self, trace_id: str, sandbox_result: dict[str, Any] | None
    ) -> None:
        self._ensure_trace(trace_id)["sandbox_result"] = sandbox_result

    def get_trace(self, trace_id: str) -> dict[str, object]:
        return dict(self._ensure_trace(trace_id))

    def _ensure_trace(self, trace_id: str) -> dict[str, object]:
        if trace_id not in self._traces:
            self._traces[trace_id] = {
                "plan": [],
                "tool_calls": [],
                "evidence": [],
                "answer": None,
                "evaluation": None,
                "memory": [],
                "sandbox_result": None,
            }
            if trace_id not in self._trace_sessions:
                root_span = TraceSpan(name="harness", kind=SpanKind.AGENT)
                self._trace_sessions[trace_id] = Trace(
                    trace_id=trace_id,
                    session_id="",
                    question="",
                    root_span=root_span,
                )
        return self._traces[trace_id]

    # ------------------------------------------------------------------
    # New nested-span API (Phase 3)
    # ------------------------------------------------------------------

    def start_trace_session(self, session_id: str, question: str) -> Trace:
        trace_id = str(uuid4())
        root_span = TraceSpan(name="harness", kind=SpanKind.AGENT)
        trace = Trace(
            trace_id=trace_id,
            session_id=session_id,
            question=question,
            root_span=root_span,
        )
        self._trace_sessions[trace_id] = trace
        # Also init legacy dict for compat
        self._traces[trace_id] = {
            "plan": [],
            "tool_calls": [],
            "evidence": [],
            "answer": None,
            "evaluation": None,
            "memory": [],
            "sandbox_result": None,
        }
        return trace

    def add_span(
        self,
        trace_id: str,
        span: TraceSpan,
        parent_span_id: str | None = None,
    ) -> None:
        """Add a span to a trace session. Appends as child of parent or root."""
        session = self._trace_sessions.get(trace_id)
        if session is None:
            return
        span.parent_span_id = parent_span_id or session.root_span.span_id
        session.root_span.children.append(span)

    def close_trace(self, trace_id: str) -> Trace | None:
        trace = self._trace_sessions.pop(trace_id, None)
        if trace is not None:
            self._persist(trace)
        return trace

    def get_trace_session(self, trace_id: str) -> Trace | None:
        return self._trace_sessions.get(trace_id)

    def _persist(self, trace: Trace) -> None:
        storage = self._storage_path
        if storage is None:
            # Derive from settings
            storage_path_str = getattr(settings, "trace_storage_path", "../data/traces")
            storage = Path(storage_path_str)
            if not storage.is_absolute():
                storage = Path(__file__).resolve().parents[3] / storage_path_str

        try:
            storage.mkdir(parents=True, exist_ok=True)
            filepath = storage / "traces.jsonl"
            with filepath.open("a", encoding="utf-8") as f:
                f.write(trace.model_dump_json() + "\n")
        except Exception as exc:
            logger.warning("Failed to persist trace %s: %s", trace.trace_id, exc)
