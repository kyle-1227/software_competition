from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, ToolCallItem
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan, TraceStatus
from app.services.tracing.repository import (
    RepositoryHealth,
    TraceRepository,
    build_trace_repository,
)
from app.services.tracing.serializers import sanitize_trace_dict, sanitize_trace_value

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
        self._healthy = True
        self._last_error: str | None = None
        self._last_error_label: str | None = None
        self._last_error_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._degraded = False
        self._ever_degraded = False

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
            status=TraceStatus.RUNNING,
            created_at=created_at,
        )
        self._trace_sessions[trace_id] = trace
        self._traces[trace_id] = _empty_flat_trace()
        self._safe_repo_call(
            "save_trace",
            self._repository.save_trace,
            trace,
            trace_id=trace_id,
        )
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
        self._safe_repo_call(
            "save_span",
            self._repository.save_span,
            trace_id,
            span,
            trace_id=trace_id,
        )

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
        trace.status = _closed_status(status, trace)
        answer = self._traces.get(trace_id, {}).get("answer")
        if isinstance(answer, str) and answer:
            trace.final_answer_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        self._closed_trace_sessions[trace_id] = trace
        self._flush_unpersisted_system_spans(trace)
        self._safe_repo_call(
            "close_trace",
            self._repository.close_trace,
            trace,
            trace_id=trace_id,
        )
        self._flush_unpersisted_system_spans(trace)
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
            self._record_repository_error("list_traces", exc)
            return list(self._closed_trace_sessions.values())[-limit:]

    def list_trace_summaries(
        self,
        limit: int = 50,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            list_summaries = getattr(self._repository, "list_trace_summaries", None)
            if callable(list_summaries):
                return list_summaries(limit, session_id, status)
        except Exception as exc:
            self._record_repository_error("list_trace_summaries", exc)
        from app.services.tracing.summary import build_trace_summary

        summaries: list[dict[str, Any]] = []
        traces = list(self._closed_trace_sessions.values())[-limit:]
        for trace in reversed(traces):
            if session_id and trace.session_id != session_id:
                continue
            if status and str(getattr(trace.status, "value", trace.status)) != str(status):
                continue
            summary = build_trace_summary(trace)
            slowest = summary.get("slowest_spans") or []
            spans = [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]
            summaries.append(
                {
                    "trace_id": trace.trace_id,
                    "session_id": trace.session_id,
                    "status": str(getattr(trace.status, "value", trace.status)),
                    "created_at": trace.created_at,
                    "closed_at": trace.closed_at,
                    "total_duration_ms": trace.total_duration_ms,
                    "question_preview": summary.get("question_preview"),
                    "span_count": summary.get("span_count", 0),
                    "error_count": summary.get("error_count", 0),
                    "degraded": any(_span_flag(span, "degraded") for span in spans),
                    "fallback_used": any(_span_flag(span, "fallback_used") for span in spans),
                    "slowest_span_name": slowest[0].get("name") if slowest else None,
                    "degraded_tool_names": summary.get("degraded_tool_names", []),
                }
            )
        return summaries

    def list_spans(self, trace_id: str) -> list[TraceSpan]:
        trace = self._trace_sessions.get(trace_id) or self._closed_trace_sessions.get(trace_id)
        if trace is not None:
            return [span for span in _walk_spans(trace.root_span) if span is not trace.root_span]
        try:
            return self._repository.list_spans(trace_id)
        except Exception as exc:
            self._record_repository_error("list_spans", exc)
            return []

    def healthcheck(self) -> bool:
        return self.health_status()["healthy"]

    def health_status(self) -> dict[str, Any]:
        try:
            health = self._repository.health_status()
        except Exception as exc:
            self._record_repository_error("health_status", exc)
            health = RepositoryHealth(
                backend="unknown",
                configured_backend=getattr(settings, "trace_backend", "auto"),
                healthy=False,
                degraded=True,
                ever_degraded=True,
                last_error=self._last_error,
                last_error_at=self._last_error_at,
                last_success_at=self._last_success_at,
                storage_path=str(self._storage_path) if self._storage_path else None,
                database_url_configured=bool(
                    getattr(settings, "trace_database_url", None)
                    or getattr(settings, "database_url", None)
                ),
                capture_mode=getattr(settings, "trace_capture_mode", "summary"),
            )
        data = health.to_dict()
        repo_healthy = bool(data.get("healthy"))
        repo_degraded = bool(data.get("degraded"))
        if repo_healthy and not repo_degraded:
            self._record_repository_success("health_status")
        else:
            self._healthy = False
            self._degraded = True
            self._ever_degraded = True
            if data.get("last_error") and not self._last_error:
                self._last_error = str(data.get("last_error"))
            if data.get("last_error_at") and self._last_error_at is None:
                self._last_error_at = _parse_datetime(data.get("last_error_at"))

        data["degraded"] = bool(data.get("degraded")) or self._degraded
        data["ever_degraded"] = bool(data.get("ever_degraded")) or self._ever_degraded
        data["healthy"] = repo_healthy and self._healthy and not data["degraded"]
        data["last_error"] = self._last_error if self._last_error else data.get("last_error")
        data["last_error_at"] = _datetime_to_json(self._last_error_at) or data.get("last_error_at")
        data["last_success_at"] = (
            _datetime_to_json(self._last_success_at) or data.get("last_success_at")
        )
        return data

    def _repository_healthcheck(self) -> bool:
        try:
            return self._repository.healthcheck()
        except Exception as exc:
            self._record_repository_error("healthcheck", exc)
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

    def _safe_repo_call(
        self,
        label: str,
        fn,
        *args,
        trace_id: str | None = None,
    ) -> bool:
        try:
            fn(*args)
            self._record_repository_success(label)
            return True
        except Exception as exc:
            self._record_repository_error(label, exc, trace_id=trace_id)
            return False

    def _record_repository_success(self, label: str) -> None:
        self._healthy = True
        self._last_success_at = datetime.now(timezone.utc)
        if label in {"health_status", "healthcheck"} and self._last_error_label not in {
            None,
            "health_status",
            "healthcheck",
        }:
            return
        if label == "flush_system_span" and self._last_error_label not in {
            None,
            "flush_system_span",
        }:
            return
        self._degraded = False
        self._last_error = None
        self._last_error_label = None

    def _record_repository_error(
        self,
        label: str,
        exc: Exception,
        trace_id: str | None = None,
    ) -> None:
        self._healthy = False
        error_summary = _error_summary(exc)
        self._last_error = f"{label}: {error_summary}"
        self._last_error_label = label
        self._last_error_at = datetime.now(timezone.utc)
        self._degraded = True
        self._ever_degraded = True
        if trace_id:
            self._record_repository_failure_span(trace_id, label, exc)
        logger.warning("Trace repository %s failed: %s", label, exc)

    def _record_repository_failure_span(
        self,
        trace_id: str,
        operation: str,
        exc: Exception,
    ) -> None:
        trace = self._trace_sessions.get(trace_id) or self._closed_trace_sessions.get(trace_id)
        if trace is None:
            logger.warning(
                "Trace repository %s failed but trace %s is not available for system span",
                operation,
                trace_id,
            )
            return
        now = datetime.now(timezone.utc)
        error_summary = _error_summary(exc)
        metadata = sanitize_trace_dict(
            {
                "trace_repository_failure": True,
                "operation": operation,
                "backend": self._repository_backend(),
                "configured_backend": self._repository_configured_backend(),
                "degraded": True,
                "synthetic": True,
                "system_span": True,
                "affects_user_answer": False,
                "persisted": False,
            }
        )
        span = TraceSpan(
            trace_id=trace_id,
            parent_span_id=trace.root_span.span_id,
            name=f"trace.repository.{operation}",
            kind=SpanKind.NODE,
            start_time=now,
            end_time=now,
            duration_ms=0,
            status=SpanStatus.ERROR,
            error=error_summary,
            error_type=exc.__class__.__name__,
            degraded=True,
            fallback_used=False,
            metadata=metadata,
        )
        trace.root_span.children.append(span)

    def _flush_unpersisted_system_spans(self, trace: Trace) -> None:
        for span in _walk_spans(trace.root_span):
            metadata = span.metadata if isinstance(span.metadata, dict) else {}
            if not (metadata.get("synthetic") is True and metadata.get("persisted") is False):
                continue
            try:
                self._repository.save_span(trace.trace_id, span)
            except Exception as exc:
                self._record_repository_error("flush_system_span", exc)
                continue
            metadata["persisted"] = True
            self._record_repository_success("flush_system_span")

    def _repository_backend(self) -> str:
        repository_name = self._repository.__class__.__name__.lower()
        if "postgres" in repository_name:
            return "postgres"
        if "jsonl" in repository_name:
            return "jsonl"
        return self._repository.__class__.__name__

    def _repository_configured_backend(self) -> str:
        return str(
            getattr(
                self._repository,
                "configured_backend",
                getattr(settings, "trace_backend", "auto"),
            )
        )


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


def _closed_status(status: str | TraceStatus | None, trace: Trace) -> TraceStatus:
    if status is not None:
        return Trace.model_validate(
            {
                "trace_id": "status-normalizer",
                "session_id": "status-normalizer",
                "question": "status-normalizer",
                "root_span": {"name": "harness", "kind": "agent"},
                "status": str(getattr(status, "value", status)),
            }
        ).status
    return TraceStatus.ERROR if _trace_has_error(trace) else TraceStatus.SUCCESS


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").lower().split())


def _span_flag(span: TraceSpan, key: str) -> bool:
    metadata = span.metadata if isinstance(span.metadata, dict) else {}
    outputs = span.outputs if isinstance(span.outputs, dict) else {}
    return bool(getattr(span, key, False) or metadata.get(key) or outputs.get(key))


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_summary(exc: Exception) -> str:
    value = sanitize_trace_value(str(exc), "error")
    return str(value or exc.__class__.__name__)[:500]


def _datetime_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
