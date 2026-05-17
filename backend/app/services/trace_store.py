from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, ToolCallItem
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan
from app.services.tracing.repository import TraceRepository, build_trace_repository

logger = logging.getLogger(__name__)


class TraceStore:
    """Execution trace store with nested spans and pluggable persistence."""

    def __init__(
        self,
        storage_path: Path | None = None,
        repository: TraceRepository | None = None,
    ) -> None:
        self._traces: dict[str, dict[str, object]] = {}
        self._trace_sessions: dict[str, Trace] = {}
        self._closed_trace_sessions: dict[str, Trace] = {}
        self._storage_path = storage_path
        self._repository = repository or build_trace_repository(storage_path)

    # ------------------------------------------------------------------
    # Flat trace compatibility API
    # ------------------------------------------------------------------

    def start_trace(
        self,
        session_id: str | None = None,
        question: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Legacy-compatible start method. Returns a trace_id string."""
        trace = self.start_trace_session(
            session_id=session_id or "default",
            question=question or "unknown",
            user_id=user_id,
            metadata=metadata,
        )
        return trace.trace_id

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

    def record_evaluation(self, trace_id: str, evaluation: EvaluationResult) -> None:
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
            self._traces[trace_id] = _empty_flat_trace()
            if trace_id not in self._trace_sessions:
                root_span = TraceSpan(name="harness", kind=SpanKind.AGENT)
                self._trace_sessions[trace_id] = Trace(
                    trace_id=trace_id,
                    session_id="default",
                    question="unknown",
                    root_span=root_span,
                    app_env=getattr(settings, "app_env", None),
                )
        return self._traces[trace_id]

    # ------------------------------------------------------------------
    # Nested-span API
    # ------------------------------------------------------------------

    def start_trace_session(
        self,
        session_id: str,
        question: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Trace:
        metadata = metadata or {}
        trace_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        root_span = TraceSpan(
            name="harness",
            kind=SpanKind.AGENT,
            trace_id=trace_id,
            start_time=created_at,
        )
        trace = Trace(
            trace_id=trace_id,
            run_id=str(uuid4()),
            session_id=session_id or "default",
            user_id=user_id,
            question=question or "unknown",
            normalized_question=_normalize_question(question or ""),
            root_span=root_span,
            app_env=getattr(settings, "app_env", None),
            app_version=str(metadata.get("app_version") or ""),
            git_commit=metadata.get("git_commit"),
            llm_provider=metadata.get("llm_provider"),
            llm_model=metadata.get("llm_model"),
            embedding_model=metadata.get("embedding_model"),
            reranker_model=metadata.get("reranker_model"),
            manual_id=metadata.get("manual_id"),
            index_version=metadata.get("index_version"),
            index_sha256=metadata.get("index_sha256"),
            feature_flags=metadata.get("feature_flags", {}),
            status="running",
            created_at=created_at,
        )
        self._trace_sessions[trace_id] = trace
        self._traces[trace_id] = _empty_flat_trace()
        self._safe_repo_call("save_trace", self._repository.save_trace, trace)
        return trace

    def add_span(
        self,
        trace_id: str,
        span: TraceSpan,
        parent_span_id: str | None = None,
    ) -> None:
        session = self._trace_sessions.get(trace_id)
        if session is None:
            return
        span.trace_id = trace_id
        if span.end_time is not None:
            duration_ms = (span.end_time - span.start_time).total_seconds() * 1000
            span.duration_ms = duration_ms
            span.metadata["duration_ms"] = duration_ms
        span.fallback_used = span.fallback_used or _truthy(
            span.metadata.get("fallback_used") or span.outputs.get("fallback_used")
        )
        span.degraded = span.degraded or _truthy(
            span.metadata.get("degraded") or span.outputs.get("degraded")
        )
        if span.attempt is None and span.metadata.get("attempt") is not None:
            span.attempt = _optional_int(span.metadata.get("attempt"))
        if span.retry_count is None and span.metadata.get("retry_count") is not None:
            span.retry_count = _optional_int(span.metadata.get("retry_count"))

        parent = self._find_span(session.root_span, parent_span_id) if parent_span_id else None
        if parent is None:
            parent = session.root_span
        span.parent_span_id = parent.span_id
        parent.children.append(span)
        self._safe_repo_call("save_span", self._repository.save_span, trace_id, span)

    def close_trace(self, trace_id: str, status: str | None = None) -> Trace | None:
        trace = self._trace_sessions.pop(trace_id, None)
        if trace is None:
            return self._closed_trace_sessions.get(trace_id)

        trace.closed_at = datetime.now(timezone.utc)
        trace.root_span.end_time = trace.closed_at
        trace.total_duration_ms = (
            trace.closed_at - trace.created_at
        ).total_seconds() * 1000
        trace.root_span.duration_ms = trace.total_duration_ms
        trace.status = status or ("error" if _trace_has_error(trace) else "ok")
        answer = self._traces.get(trace_id, {}).get("answer")
        if isinstance(answer, str) and answer:
            trace.final_answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        self._safe_repo_call("close_trace", self._repository.close_trace, trace)
        self._closed_trace_sessions[trace_id] = trace
        return trace

    def get_trace_session(self, trace_id: str) -> Trace | None:
        return self._trace_sessions.get(trace_id)

    def get_trace_tree(self, trace_id: str) -> Trace | None:
        trace = self._trace_sessions.get(trace_id) or self._closed_trace_sessions.get(trace_id)
        if trace is not None:
            return trace
        try:
            return self._repository.get_trace(trace_id)
        except Exception as exc:
            logger.warning("Failed to load trace %s from repository: %s", trace_id, exc)
            return None

    def list_traces(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[Trace]:
        try:
            return self._repository.list_traces(limit, session_id, status)
        except Exception as exc:
            logger.warning("Failed to list traces from repository: %s", exc)
            return list(self._closed_trace_sessions.values())[-limit:]

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        trace = self._trace_sessions.get(trace_id) or self._closed_trace_sessions.get(trace_id)
        if trace is not None:
            return [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]
        try:
            return self._repository.list_spans(trace_id)
        except Exception as exc:
            logger.warning("Failed to list spans for trace %s: %s", trace_id, exc)
            return []

    def healthcheck(self) -> bool:
        try:
            return self._repository.healthcheck()
        except Exception:
            return False

    def _find_span(self, span: TraceSpan, span_id: str | None) -> TraceSpan | None:
        if not span_id:
            return None
        if span.span_id == span_id:
            return span
        for child in span.children:
            found = self._find_span(child, span_id)
            if found is not None:
                return found
        return None

    @staticmethod
    def _safe_repo_call(label: str, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            logger.warning("Trace repository %s failed: %s", label, exc)


def _empty_flat_trace() -> dict[str, object]:
    return {
        "plan": [],
        "tool_calls": [],
        "evidence": [],
        "answer": None,
        "evaluation": None,
        "memory": [],
        "sandbox_result": None,
    }


def _walk_spans(span: TraceSpan):
    yield span
    for child in span.children:
        yield from _walk_spans(child)


def _trace_has_error(trace: Trace) -> bool:
    return any(span.status == SpanStatus.ERROR for span in _walk_spans(trace.root_span))


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").lower().split())


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
