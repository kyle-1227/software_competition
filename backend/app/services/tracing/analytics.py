from __future__ import annotations

from enum import Enum
from typing import Any, Callable


class FailureType(str, Enum):
    SUCCESS = "success"
    RETRIEVAL_FAILURE = "retrieval_failure"
    RERANKER_FAILURE = "reranker_failure"
    LLM_FAILURE = "llm_failure"
    TOOL_FAILURE = "tool_failure"
    SANDBOX_REJECTED = "sandbox_rejected"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    POLICY_APPROVAL_REQUIRED = "policy_approval_required"
    EVALUATOR_LOW_CONFIDENCE = "evaluator_low_confidence"
    MEMORY_FAILURE = "memory_failure"
    TRACE_REPOSITORY_FAILURE = "trace_repository_failure"
    FALLBACK_DEGRADED = "fallback_degraded"
    UNKNOWN_FAILURE = "unknown_failure"


def build_trace_analytics(trace: Any) -> dict[str, Any]:
    classification = classify_failure(trace)
    spans = _observed_spans(trace)
    bottleneck = _bottleneck_span(spans)
    degraded = any(_span_flag(span, "degraded") for span in spans)
    fallback_used = any(_span_flag(span, "fallback_used") for span in spans)
    return {
        **classification,
        "bottleneck_span": bottleneck,
        "total_duration_ms": _duration_ms(getattr(trace, "root_span", None))
        or getattr(trace, "total_duration_ms", None),
        "degraded": degraded,
        "fallback_used": fallback_used,
    }


def classify_failure(trace: Any) -> dict[str, Any]:
    spans = _observed_spans(trace)
    if not spans and trace is not None:
        return _classification(
            FailureType.UNKNOWN_FAILURE,
            None,
            "Trace contains no observable spans.",
            "Inspect trace ingestion and span recording configuration.",
        )

    rules: list[tuple[FailureType, Callable[[Any], bool], str, str]] = [
        (
            FailureType.RETRIEVAL_FAILURE,
            _is_retrieval_failure,
            "Retrieval produced no effective evidence or placeholder evidence.",
            "Check vector index build, chunking, embedding model, query rewrite, and retriever filters.",
        ),
        (
            FailureType.RERANKER_FAILURE,
            _is_reranker_failure,
            "Reranker failed or selected no usable candidates.",
            "Check reranker provider, candidate count, top_n, and fallback path.",
        ),
        (
            FailureType.LLM_FAILURE,
            _is_llm_failure,
            "LLM generation failed or used a local fallback.",
            "Check LLM provider configuration, model availability, quota, and timeout settings.",
        ),
        (
            FailureType.TOOL_FAILURE,
            _is_tool_failure,
            "A tool span failed after retries.",
            "Inspect tool retry attempt spans, payload summaries, and upstream dependency health.",
        ),
        (
            FailureType.SANDBOX_REJECTED,
            _is_sandbox_rejected,
            "Sandbox rejected or failed execution.",
            "Check sandbox policy, language, timeout, return code, and generated script safety.",
        ),
        (
            FailureType.GUARDRAIL_BLOCKED,
            _is_guardrail_blocked,
            "Guardrail blocked the request or response.",
            "Review safety policy configuration and blocked input/output categories.",
        ),
        (
            FailureType.POLICY_APPROVAL_REQUIRED,
            _is_approval_required,
            "Human approval was required by policy.",
            "Review high-risk actions, low-evidence conclusions, and sandbox outcomes.",
        ),
        (
            FailureType.EVALUATOR_LOW_CONFIDENCE,
            _is_evaluator_low_confidence,
            "Evaluator confidence was below threshold.",
            "Check evidence coverage, answer citations, and evaluator feedback.",
        ),
        (
            FailureType.MEMORY_FAILURE,
            _is_memory_failure,
            "Memory load or save failed.",
            "Check memory storage connectivity and serialization errors.",
        ),
        (
            FailureType.TRACE_REPOSITORY_FAILURE,
            _is_trace_repository_failure,
            "Trace repository degraded or failed.",
            "Check trace repository health, database connectivity, and migration state.",
        ),
        (
            FailureType.FALLBACK_DEGRADED,
            _is_fallback_degraded,
            "Fallback or degraded mode was used.",
            "Inspect degraded spans and upstream service availability.",
        ),
    ]
    for failure_type, predicate, reason, suggested_fix in rules:
        span = next((item for item in spans if predicate(item)), None)
        if span is not None:
            return _classification(failure_type, span, reason, suggested_fix)

    status = _enum_value(getattr(trace, "status", None))
    if status == "error":
        return _classification(
            FailureType.UNKNOWN_FAILURE,
            None,
            "Trace ended in error but no specific failure signal matched.",
            "Inspect the trace timeline and root span error fields.",
        )
    return _classification(
        FailureType.SUCCESS,
        None,
        "No dominant failure signal was found.",
        "No action required unless the answer quality is still below expectations.",
    )


def _classification(
    failure_type: FailureType,
    span: Any,
    reason: str,
    suggested_fix: str,
) -> dict[str, Any]:
    return {
        "failure_type": failure_type.value,
        "root_cause_span": _span_ref(span) if span is not None else None,
        "reason": reason,
        "suggested_fix": suggested_fix,
    }


def _observed_spans(trace: Any) -> list[Any]:
    root = getattr(trace, "root_span", None)
    if root is None:
        return []
    return [span for span in _walk_spans(root) if span is not root]


def _walk_spans(span: Any):
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _is_retrieval_failure(span: Any) -> bool:
    name = _name(span)
    data = _data(span)
    if name == "retriever.vector_search":
        return bool(data.get("placeholder_used")) or int(data.get("evidence_count") or 0) == 0
    return name == "node.retrieval_retry" and _is_error(span)


def _is_reranker_failure(span: Any) -> bool:
    if _name(span) != "reranker.score":
        return False
    data = _data(span)
    selected = data.get("selected_count")
    return _is_error(span) or selected == 0 or data.get("degraded") is True


def _is_llm_failure(span: Any) -> bool:
    return _name(span).startswith("llm.") and (
        _is_error(span) or bool(_data(span).get("local_fallback"))
    )


def _is_tool_failure(span: Any) -> bool:
    return _name(span).startswith("tool.") and _is_error(span)


def _is_sandbox_rejected(span: Any) -> bool:
    if not _name(span).startswith("sandbox."):
        return False
    data = _data(span)
    return _is_error(span) or data.get("allowed") is False or data.get("return_code") not in (None, 0)


def _is_guardrail_blocked(span: Any) -> bool:
    name = _name(span)
    data = _data(span)
    return "guardrail" in name and (_is_error(span) or data.get("blocked") is True)


def _is_approval_required(span: Any) -> bool:
    return _name(span) == "node.approval" or bool(
        _data(span).get("requires_human_approval")
    )


def _is_evaluator_low_confidence(span: Any) -> bool:
    if _name(span) != "evaluator.optimizer":
        return False
    confidence = (
        _data(span).get("final_confidence")
        or _data(span).get("confidence")
        or _data(span).get("best_confidence")
    )
    try:
        return float(confidence) < 0.7
    except (TypeError, ValueError):
        return False


def _is_memory_failure(span: Any) -> bool:
    return (_name(span).startswith("node.memory") or _enum_value(getattr(span, "kind", None)) == "memory") and _is_error(span)


def _is_trace_repository_failure(span: Any) -> bool:
    data = _data(span)
    return _name(span).startswith("trace.repository") or data.get("trace_repository_failure") is True


def _is_fallback_degraded(span: Any) -> bool:
    return _span_flag(span, "degraded") or _span_flag(span, "fallback_used")


def _bottleneck_span(spans: list[Any]) -> dict[str, Any] | None:
    timed = [(span, _duration_ms(span)) for span in spans]
    timed = [(span, duration) for span, duration in timed if duration is not None]
    if not timed:
        return None
    span, duration = max(timed, key=lambda item: item[1])
    return {**_span_ref(span), "duration_ms": duration}


def _span_ref(span: Any) -> dict[str, Any]:
    return {
        "span_id": getattr(span, "span_id", None),
        "name": _name(span),
        "kind": _enum_value(getattr(span, "kind", None)),
        "status": _enum_value(getattr(span, "status", None)),
    }


def _span_flag(span: Any, key: str) -> bool:
    return bool(getattr(span, key, False) or _data(span).get(key))


def _is_error(span: Any) -> bool:
    return _enum_value(getattr(span, "status", None)) == "error" or bool(
        getattr(span, "error", None)
    )


def _duration_ms(span: Any) -> float | None:
    if span is None:
        return None
    value = getattr(span, "duration_ms", None) or _data(span).get("duration_ms")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _data(span: Any) -> dict[str, Any]:
    metadata = getattr(span, "metadata", None)
    outputs = getattr(span, "outputs", None)
    return {
        **(metadata if isinstance(metadata, dict) else {}),
        **(outputs if isinstance(outputs, dict) else {}),
    }


def _name(span: Any) -> str:
    return str(getattr(span, "name", "") or "")


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
