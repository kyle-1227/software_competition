from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field

from app.services.tracing.serializers import redact_trace_text

from app.schemas.trace import Trace
from app.services.tracing.analytics import FailureType, build_trace_analytics
from app.services.tracing.persistence import question_persistence_fields
from app.services.tracing.serializers import (
    redact_trace_text,
    resolve_capture_mode,
    sanitize_trace_dict,
)
from app.services.tracing.summary import build_trace_summary

TRACE_EVAL_CASE_SCHEMA_VERSION = "trace_eval_case.v1"
TraceEvalSource = Literal["api_export", "cli_export", "trace_export"]


class TraceEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = TRACE_EVAL_CASE_SCHEMA_VERSION
    case_id: str
    trace_id: str
    source: TraceEvalSource = "trace_export"
    created_at: str
    question_hash: str | None = None
    question_preview: str | None = None
    session_id: str | None = None
    failure_type: str
    root_cause_span: dict[str, Any] | None = None
    suggested_fix: str | None = None
    reason: str | None = None
    trace_status: str
    degraded: bool = False
    fallback_used: bool = False
    confidence: float | None = None
    evidence_count: int = 0
    retrieved_pages: list[int] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    guardrail_blocked: bool = False
    approval_required: bool = False
    expected_behavior: str
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


EXPECTED_BEHAVIOR_BY_FAILURE_TYPE = {
    FailureType.SUCCESS.value: "System should preserve this successful grounded behavior.",
    FailureType.RETRIEVAL_FAILURE.value: (
        "System should not fabricate an answer when retrieval returns no grounded evidence. "
        "It should ask for more device/manual context or report insufficient evidence."
    ),
    FailureType.RERANKER_FAILURE.value: (
        "System should surface reranker degradation and avoid treating unranked candidates "
        "as verified evidence."
    ),
    FailureType.LLM_FAILURE.value: (
        "System should surface model generation failure and avoid returning unsupported "
        "fallback content as grounded output."
    ),
    FailureType.TOOL_FAILURE.value: (
        "System should expose tool failure, retry within budget, and avoid presenting "
        "failed tool output as verified result."
    ),
    FailureType.SANDBOX_REJECTED.value: (
        "System should reject unsafe code execution and avoid running unapproved scripts."
    ),
    FailureType.GUARDRAIL_BLOCKED.value: (
        "System should block unsafe input/output and provide a safe explanation."
    ),
    FailureType.POLICY_APPROVAL_REQUIRED.value: (
        "System should request human approval for high-risk or low-evidence actions."
    ),
    FailureType.EVALUATOR_LOW_CONFIDENCE.value: (
        "System should improve or decline the answer when evaluator confidence is below threshold."
    ),
    FailureType.MEMORY_FAILURE.value: (
        "System should surface memory persistence failure without corrupting the active answer."
    ),
    FailureType.TRACE_REPOSITORY_FAILURE.value: (
        "System should preserve user-facing task status while surfacing trace repository "
        "failure through observability health and analytics."
    ),
    FailureType.FALLBACK_DEGRADED.value: (
        "System should clearly mark degraded fallback behavior and avoid overstating certainty."
    ),
    FailureType.UNKNOWN_FAILURE.value: (
        "System should expose enough trace evidence for a human to classify the failure."
    ),
}


def trace_to_eval_case(
    trace: Trace,
    *,
    source: TraceEvalSource = "trace_export",
    analytics: dict[str, Any] | None = None,
    include_success: bool = False,
) -> dict[str, Any]:
    """Convert a trace to a deterministic, redacted eval case dictionary."""

    analytics = analytics or build_trace_analytics(trace)
    summary = build_trace_summary(trace)
    failure_type = str(analytics.get("failure_type") or FailureType.UNKNOWN_FAILURE.value)
    if failure_type == FailureType.SUCCESS.value and not include_success:
        # The case is still buildable for API consistency, but callers should use
        # should_export_trace_to_eval() to decide whether to persist it.
        pass

    question_hash = _question_hash(trace)
    root_cause_span = _safe_span_ref(analytics.get("root_cause_span"))
    case = TraceEvalCase(
        case_id=_case_id(
            trace_id=str(getattr(trace, "trace_id", "") or ""),
            failure_type=failure_type,
            root_cause_span_id=(root_cause_span or {}).get("span_id"),
            question_hash=question_hash,
        ),
        trace_id=str(getattr(trace, "trace_id", "") or ""),
        source=source,
        created_at=_case_created_at(trace),
        question_hash=question_hash,
        question_preview=_question_preview(trace),
        session_id=getattr(trace, "session_id", None),
        failure_type=failure_type,
        root_cause_span=root_cause_span,
        suggested_fix=_safe_text(analytics.get("suggested_fix")),
        reason=_safe_text(analytics.get("reason")),
        trace_status=_enum_value(getattr(trace, "status", None)) or "unknown",
        degraded=bool(analytics.get("degraded")),
        fallback_used=bool(analytics.get("fallback_used")),
        confidence=_confidence(summary),
        evidence_count=int((summary.get("retrieval") or {}).get("evidence_count") or 0),
        retrieved_pages=_retrieved_pages(summary),
        tool_names=_tool_names(trace),
        guardrail_blocked=_guardrail_blocked(trace, analytics),
        approval_required=_approval_required(summary, analytics),
        expected_behavior=_expected_behavior(failure_type),
        assertions=_assertions_for_failure(failure_type, summary, analytics),
        metadata=_metadata(trace),
    )
    return case.model_dump(mode="json")


def should_export_trace_to_eval(
    trace: Trace,
    analytics: dict[str, Any] | None = None,
    *,
    include_success: bool = False,
) -> bool:
    analytics = analytics or build_trace_analytics(trace)
    summary = build_trace_summary(trace)
    failure_type = str(analytics.get("failure_type") or FailureType.UNKNOWN_FAILURE.value)
    if failure_type != FailureType.SUCCESS.value:
        return True
    if include_success:
        return True
    if _enum_value(getattr(trace, "status", None)) == "error":
        return True
    if bool(analytics.get("degraded")) or bool(analytics.get("fallback_used")):
        return True
    if int((summary.get("retrieval") or {}).get("evidence_count") or 0) == 0:
        return True
    confidence = _confidence(summary)
    if confidence is not None and confidence < 0.7:
        return True
    if _guardrail_blocked(trace, analytics) or _approval_required(summary, analytics):
        return True
    return False


def _case_id(
    *,
    trace_id: str,
    failure_type: str,
    root_cause_span_id: str | None,
    question_hash: str | None,
) -> str:
    material = "|".join(
        (
            trace_id,
            failure_type,
            root_cause_span_id or "",
            question_hash or "",
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()[:32]


def _question_hash(trace: Trace) -> str | None:
    if getattr(trace, "question_hash", None):
        return str(trace.question_hash)
    fields = question_persistence_fields(getattr(trace, "question", None))
    return fields.get("question_hash")


def _question_preview(trace: Trace) -> str | None:
    mode = resolve_capture_mode()
    if mode == "minimal":
        return None
    value = getattr(trace, "question_preview", None) or getattr(trace, "question", None)
    if value is None:
        return None
    return _non_full_preview(str(value), 500 if mode == "debug" else 120)


def _non_full_preview(value: str, limit: int) -> str | None:
    redacted = redact_trace_text(value)
    if not redacted:
        return None
    if len(redacted) <= 1:
        return redacted
    max_chars = min(limit, len(redacted) - 1)
    return redacted[:max_chars].rstrip() + "..."


def _safe_span_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        key: value.get(key)
        for key in ("span_id", "name", "kind", "status", "duration_ms")
        if value.get(key) is not None
    }
    return sanitize_trace_dict(allowed)


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    return _non_full_preview(str(value), 240)


def _case_created_at(trace: Trace) -> str:
    value = getattr(trace, "closed_at", None) or getattr(trace, "created_at", None)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _metadata(trace: Trace) -> dict[str, Any]:
    created_from = getattr(trace, "closed_at", None) or getattr(trace, "created_at", None)
    metadata = {
        "app_env": getattr(trace, "app_env", None),
        "app_version": getattr(trace, "app_version", None),
        "git_commit": getattr(trace, "git_commit", None),
        "llm_model": getattr(trace, "llm_model", None),
        "manual_id": getattr(trace, "manual_id", None),
        "index_version": getattr(trace, "index_version", None),
        "created_from_trace_at": created_from.isoformat()
        if isinstance(created_from, datetime)
        else created_from,
    }
    return sanitize_trace_dict({key: value for key, value in metadata.items() if value is not None})


def _confidence(summary: dict[str, Any]) -> float | None:
    value = (summary.get("evaluator") or {}).get("confidence")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _retrieved_pages(summary: dict[str, Any]) -> list[int]:
    pages = (summary.get("retrieval") or {}).get("retrieved_pages") or []
    result: list[int] = []
    for page in pages:
        try:
            result.append(int(page))
        except (TypeError, ValueError):
            continue
    return result


def _tool_names(trace: Trace) -> list[str]:
    names: list[str] = []
    for span in _walk_spans(getattr(trace, "root_span", None)):
        if span is getattr(trace, "root_span", None):
            continue
        name = str(getattr(span, "name", "") or "")
        metadata = getattr(span, "metadata", None)
        tool_name = metadata.get("tool_name") if isinstance(metadata, dict) else None
        if tool_name:
            candidate = str(tool_name)
        elif name.startswith("tool."):
            parts = name.split(".")
            candidate = parts[1] if len(parts) > 1 else name
        else:
            continue
        if candidate not in names:
            names.append(candidate)
    return names


def _guardrail_blocked(trace: Trace, analytics: dict[str, Any]) -> bool:
    if analytics.get("failure_type") == FailureType.GUARDRAIL_BLOCKED.value:
        return True
    for span in _walk_spans(getattr(trace, "root_span", None)):
        name = str(getattr(span, "name", "") or "")
        data = _span_data(span)
        if "guardrail" in name and (
            _enum_value(getattr(span, "status", None)) == "error" or data.get("blocked") is True
        ):
            return True
    return False


def _approval_required(summary: dict[str, Any], analytics: dict[str, Any]) -> bool:
    return (
        analytics.get("failure_type") == FailureType.POLICY_APPROVAL_REQUIRED.value
        or bool((summary.get("agent_loop") or {}).get("approval_triggered"))
        or bool((summary.get("safety") or {}).get("approval_triggered"))
    )


def _expected_behavior(failure_type: str) -> str:
    return EXPECTED_BEHAVIOR_BY_FAILURE_TYPE.get(
        failure_type,
        EXPECTED_BEHAVIOR_BY_FAILURE_TYPE[FailureType.UNKNOWN_FAILURE.value],
    )


def _assertions_for_failure(
    failure_type: str,
    summary: dict[str, Any],
    analytics: dict[str, Any],
) -> list[dict[str, Any]]:
    retrieval = summary.get("retrieval") or {}
    assertions: list[dict[str, Any]] = [
        {"type": "failure_type", "expected": failure_type},
    ]
    if failure_type == FailureType.RETRIEVAL_FAILURE.value:
        assertions.extend(
            [
                {"type": "requires_grounded_evidence", "expected": True},
                {
                    "type": "no_fabrication_without_evidence",
                    "expected": True,
                },
                {
                    "type": "retrieval_missing_or_placeholder",
                    "expected": bool(retrieval.get("placeholder_used"))
                    or int(retrieval.get("evidence_count") or 0) == 0,
                },
            ]
        )
    elif failure_type == FailureType.TOOL_FAILURE.value:
        assertions.extend(
            [
                {"type": "failed_tool_span_exists", "expected": True},
                {"type": "failed_tool_output_not_verified", "expected": True},
            ]
        )
    elif failure_type == FailureType.TRACE_REPOSITORY_FAILURE.value:
        assertions.extend(
            [
                {"type": "synthetic_system_span_exists", "expected": True},
                {"type": "health_exposes_degraded_or_ever_degraded", "expected": True},
            ]
        )
    elif failure_type == FailureType.GUARDRAIL_BLOCKED.value:
        assertions.extend(
            [
                {"type": "blocked", "expected": True},
                {"type": "unsafe_output_not_returned", "expected": True},
            ]
        )
    elif failure_type == FailureType.SANDBOX_REJECTED.value:
        assertions.extend(
            [
                {"type": "sandbox_rejected_or_failed", "expected": True},
                {"type": "unsafe_code_not_executed", "expected": True},
            ]
        )
    elif failure_type == FailureType.EVALUATOR_LOW_CONFIDENCE.value:
        assertions.append({"type": "confidence_below_threshold", "expected": True})
    elif failure_type == FailureType.LLM_FAILURE.value:
        assertions.append({"type": "unsupported_fallback_not_grounded", "expected": True})
    elif failure_type == FailureType.SUCCESS.value:
        assertions.append({"type": "preserve_successful_grounded_behavior", "expected": True})
    if analytics.get("degraded"):
        assertions.append({"type": "degraded_trace", "expected": True})
    if analytics.get("fallback_used"):
        assertions.append({"type": "fallback_used", "expected": True})
    return assertions


def _walk_spans(span: Any):
    if span is None:
        return
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _span_data(span: Any) -> dict[str, Any]:
    metadata = getattr(span, "metadata", None)
    outputs = getattr(span, "outputs", None)
    return {
        **(metadata if isinstance(metadata, dict) else {}),
        **(outputs if isinstance(outputs, dict) else {}),
    }


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


# ── Eval export (originally from scripts/export_trace_eval_cases.py) ─────────


@dataclass
class EvalExportOptions:
    dataset: Path
    trace_id: str | None = None
    session_id: str | None = None
    status: str | None = None
    failure_type: str | None = None
    limit: int = 100
    include_success: bool = False
    database_url: str | None = None
    trace_backend: str = "auto"
    apply: bool = False
    verbose: bool = False


@dataclass
class EvalExportStats:
    traces_seen: int = 0
    eligible: int = 0
    exported: int = 0
    deduplicated: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def export_trace_eval_cases(
    options: EvalExportOptions,
    *,
    repository: Any | None = None,
    writer: Any | None = None,
    stderr: TextIO | None = None,
) -> EvalExportStats:
    from app.services.tracing.analytics import build_trace_analytics
    from app.services.tracing.eval_dataset import (
        TraceEvalDatasetWriter,
    )
    from app.services.tracing.repository import (
        JsonlTraceRepository,
        PostgreSQLTraceRepository,
        build_trace_repository,
    )

    stats = EvalExportStats(dry_run=not options.apply)
    try:
        if repository is None:
            repository = _build_export_repository(options)
        else:
            repository = repository
    except Exception as exc:
        stats.failed += 1
        _warn_export(stderr, "repository_initialize_failed", error=exc)
        return stats

    try:
        traces = _candidate_export_traces(repository, options)
    except Exception as exc:
        stats.failed += 1
        _warn_export(stderr, "trace_listing_failed", error=exc)
        return stats

    writer = writer or TraceEvalDatasetWriter(options.dataset)
    for trace in traces:
        if trace is None:
            stats.skipped += 1
            continue
        stats.traces_seen += 1
        try:
            full_trace = _load_full_export_trace(repository, trace)
            if full_trace is None:
                stats.skipped += 1
                continue
            analytics = build_trace_analytics(full_trace)
            failure_type = str(analytics.get("failure_type") or "")
            if options.failure_type and failure_type != options.failure_type:
                stats.skipped += 1
                continue
            eligible = should_export_trace_to_eval(
                full_trace, analytics, include_success=options.include_success,
            )
            if not eligible:
                stats.skipped += 1
                continue
            stats.eligible += 1
            case = trace_to_eval_case(
                full_trace, source="cli_export", analytics=analytics,
                include_success=options.include_success,
            )
            if not options.apply:
                continue
            if writer.append_case(case):
                stats.exported += 1
            else:
                stats.deduplicated += 1
        except Exception as exc:
            stats.failed += 1
            _warn_export(stderr, "trace_eval_case_export_failed", trace_id=getattr(trace, "trace_id", None), error=exc)
            continue
    return stats


def _candidate_export_traces(repository: Any, options: EvalExportOptions) -> list[Any]:
    if options.trace_id:
        return [repository.get_trace(options.trace_id)]
    traces = repository.list_traces(
        limit=options.limit, session_id=options.session_id, status=options.status,
    )
    return list(traces or [])[:options.limit]


def _load_full_export_trace(repository: Any, trace: Any) -> Any | None:
    trace_id = getattr(trace, "trace_id", None)
    if not trace_id:
        return None
    loaded = repository.get_trace(trace_id)
    return loaded or trace


def _build_export_repository(options: EvalExportOptions) -> Any:
    from app.services.tracing.repository import (
        JsonlTraceRepository,
        PostgreSQLTraceRepository,
        build_trace_repository,
    )

    if options.trace_backend == "postgres" or (
        options.trace_backend == "auto" and options.database_url
    ):
        if not options.database_url:
            raise RuntimeError("database_url is required for postgres trace export")
        repository = PostgreSQLTraceRepository(str(options.database_url), configured_backend=options.trace_backend)
        repository.initialize()
        return repository
    if options.trace_backend == "jsonl":
        repository = JsonlTraceRepository(configured_backend="jsonl")
        repository.initialize()
        return repository
    return build_trace_repository()


def _warn_export(
    stderr: TextIO | None,
    event: str,
    *,
    trace_id: str | None = None,
    error: Exception | None = None,
) -> None:
    if stderr is None:
        return
    payload: dict[str, Any] = {"event": event}
    if trace_id:
        payload["trace_id"] = str(trace_id)
    if error is not None:
        payload["error_type"] = error.__class__.__name__
        payload["error_summary"] = redact_trace_text(str(error))[:500]
    stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
