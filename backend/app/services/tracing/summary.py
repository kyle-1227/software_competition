from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def build_trace_summary(trace: Any) -> dict[str, Any]:
    if trace is None:
        return _empty_summary()

    root_span = getattr(trace, "root_span", None)
    spans = list(_walk_spans(root_span)) if root_span is not None else []
    observed_spans = [span for span in spans if span is not root_span]
    summary = _empty_summary()
    summary["trace_id"] = getattr(trace, "trace_id", None)
    summary["session_id"] = getattr(trace, "session_id", None)
    summary["question_preview"] = _preview(getattr(trace, "question", None))
    summary["span_count"] = len(observed_spans)
    summary["error_count"] = sum(1 for span in observed_spans if _is_error(span))
    summary["total_duration_ms"] = _trace_duration_ms(trace, observed_spans)
    summary["slowest_spans"] = _slowest_spans(observed_spans)
    summary["tool_attempt_count"] = sum(
        1 for span in observed_spans if _is_tool_attempt(span)
    )

    degraded_tools: list[str] = []
    retrieved_pages: list[Any] = []
    evidence_count = 0
    placeholder_used = False
    retrieval_retry_count = 0
    answer_regeneration_count = 0
    loop_decision_count = 0
    guardrail_error_count = 0

    for span in observed_spans:
        name = str(getattr(span, "name", "") or "")
        metadata = _mapping(getattr(span, "metadata", None))
        outputs = _mapping(getattr(span, "outputs", None))
        combined = {**metadata, **outputs}

        if name.startswith("tool.") and _truthy(combined.get("degraded")):
            _append_unique(degraded_tools, _tool_name_from_span(name, metadata))

        if name == "retriever.query_rewrite":
            if _truthy(combined.get("hyde_enabled")):
                summary["retrieval"]["hyde_used"] = True

        if name == "retriever.vector_search":
            _extend_unique(retrieved_pages, _list_value(combined.get("retrieved_pages")))
            evidence_count = max(evidence_count, _int_value(combined.get("evidence_count")))
            placeholder_used = placeholder_used or _truthy(combined.get("placeholder_used"))

        if name == "reranker.score":
            summary["retrieval"]["reranker_used"] = True

        if name == "llm.answer_generation":
            summary["llm"]["answer_generation_count"] += 1
            model = combined.get("llm_model") or combined.get("model")
            if model:
                summary["llm"]["model"] = str(model)
            fallback_used = _truthy(combined.get("fallback_used"))
            local_fallback = _truthy(combined.get("local_fallback"))
            summary["llm"]["fallback_used"] = (
                summary["llm"]["fallback_used"] or fallback_used or local_fallback
            )
            summary["llm"]["local_fallback"] = (
                summary["llm"]["local_fallback"] or local_fallback
            )

        if name == "evaluator.optimizer":
            _merge_evaluator_summary(summary["evaluator"], combined)
            answer_regeneration_count = max(
                answer_regeneration_count,
                _int_value(combined.get("answer_regeneration_count")),
            )

        if name in {"node.loop_decision", "node.post_eval_loop_decision"}:
            loop_decision_count += 1
        if name == "node.retrieval_retry":
            retrieval_retry_count += 1
        if name == "node.answer_regeneration":
            answer_regeneration_count += 1
        if name == "node.approval" or _truthy(combined.get("requires_human_approval")):
            summary["agent_loop"]["approval_triggered"] = True
            summary["safety"]["approval_triggered"] = True
        if name == "node.fail_safe":
            summary["agent_loop"]["fail_safe_triggered"] = True
            summary["safety"]["fail_safe_triggered"] = True
        if name == "node.clarification":
            summary["agent_loop"]["clarification_triggered"] = True
        if "guardrail" in name and _is_error(span):
            guardrail_error_count += 1

    summary["degraded_tool_names"] = degraded_tools
    summary["retrieval"]["retrieved_pages"] = retrieved_pages
    summary["retrieval"]["evidence_count"] = evidence_count
    summary["retrieval"]["placeholder_used"] = placeholder_used
    summary["retrieval"]["retrieval_retry_triggered"] = retrieval_retry_count > 0
    summary["agent_loop"]["loop_decision_count"] = (
        loop_decision_count if loop_decision_count else None
    )
    summary["agent_loop"]["retrieval_retry_count"] = (
        retrieval_retry_count if retrieval_retry_count else None
    )
    summary["agent_loop"]["answer_regeneration_count"] = (
        answer_regeneration_count if answer_regeneration_count else None
    )
    summary["safety"]["guardrail_error_count"] = guardrail_error_count
    return summary


def _empty_summary() -> dict[str, Any]:
    return {
        "trace_id": None,
        "session_id": None,
        "question_preview": None,
        "span_count": 0,
        "error_count": 0,
        "total_duration_ms": None,
        "slowest_spans": [],
        "tool_attempt_count": 0,
        "degraded_tool_names": [],
        "retrieval": {
            "retrieved_pages": [],
            "evidence_count": 0,
            "placeholder_used": False,
            "retrieval_retry_triggered": False,
            "reranker_used": False,
            "hyde_used": False,
        },
        "llm": {
            "answer_generation_count": 0,
            "model": None,
            "fallback_used": False,
            "local_fallback": False,
        },
        "evaluator": {
            "confidence": None,
            "iteration_count": None,
            "issues_count": None,
            "compliance_attempts": None,
            "compliance_success": None,
            "compliance_degraded": False,
        },
        "agent_loop": {
            "loop_decision_count": None,
            "retrieval_retry_count": None,
            "answer_regeneration_count": None,
            "approval_triggered": False,
            "fail_safe_triggered": False,
            "clarification_triggered": False,
        },
        "safety": {
            "approval_triggered": False,
            "fail_safe_triggered": False,
            "guardrail_error_count": 0,
        },
    }


def _walk_spans(span: Any):
    if span is None:
        return
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _trace_duration_ms(trace: Any, spans: list[Any]) -> float | None:
    trace_duration = _float_or_none(getattr(trace, "total_duration_ms", None))
    if trace_duration is not None:
        return trace_duration
    root = getattr(trace, "root_span", None)
    root_duration = _duration_ms(root)
    if root_duration is not None:
        return root_duration

    start_times = [_datetime(getattr(span, "start_time", None)) for span in spans]
    end_times = [_datetime(getattr(span, "end_time", None)) for span in spans]
    start_values = [value for value in start_times if value is not None]
    end_values = [value for value in end_times if value is not None]
    if not start_values or not end_values:
        return None
    return round((max(end_values) - min(start_values)).total_seconds() * 1000, 3)


def _slowest_spans(spans: list[Any]) -> list[dict[str, Any]]:
    timed = [
        (span, duration)
        for span in spans
        if (duration := _duration_ms(span)) is not None
    ]
    timed.sort(key=lambda item: item[1], reverse=True)
    return [
        {
            "name": str(getattr(span, "name", "") or ""),
            "kind": _enum_value(getattr(span, "kind", None)),
            "status": _enum_value(getattr(span, "status", None)),
            "duration_ms": duration,
        }
        for span, duration in timed[:5]
    ]


def _merge_evaluator_summary(target: dict[str, Any], data: dict[str, Any]) -> None:
    confidence = _float_or_none(
        data.get("final_confidence")
        if data.get("final_confidence") is not None
        else data.get("best_confidence")
        if data.get("best_confidence") is not None
        else data.get("confidence")
    )
    if confidence is not None:
        target["confidence"] = confidence

    for source_key, target_key in (
        ("iteration_count", "iteration_count"),
        ("issues_count", "issues_count"),
        ("compliance_attempts", "compliance_attempts"),
    ):
        value = _int_or_none(data.get(source_key))
        if value is not None:
            target[target_key] = value

    if data.get("compliance_success") is not None:
        target["compliance_success"] = _truthy(data.get("compliance_success"))
    target["compliance_degraded"] = target["compliance_degraded"] or _truthy(
        data.get("compliance_degraded")
    )


def _is_tool_attempt(span: Any) -> bool:
    name = str(getattr(span, "name", "") or "")
    return name.startswith("tool.") and name.endswith(".attempt")


def _tool_name_from_span(name: str, metadata: dict[str, Any]) -> str:
    if metadata.get("tool_name"):
        return str(metadata["tool_name"])
    parts = name.split(".")
    return parts[1] if len(parts) >= 2 else name


def _is_error(span: Any) -> bool:
    return _enum_value(getattr(span, "status", None)) == "error" or bool(
        getattr(span, "error", None)
    )


def _duration_ms(span: Any) -> float | None:
    if span is None:
        return None
    attr_value = _float_or_none(getattr(span, "duration_ms", None))
    if attr_value is not None:
        return attr_value
    metadata = _mapping(getattr(span, "metadata", None))
    value = _float_or_none(metadata.get("duration_ms"))
    if value is not None:
        return value
    start = _datetime(getattr(span, "start_time", None))
    end = _datetime(getattr(span, "end_time", None))
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000, 3)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _extend_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        _append_unique(target, value)


def _append_unique(target: list[Any], value: Any) -> None:
    if value is not None and value not in target:
        target.append(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    return _int_or_none(value) or 0


def _preview(value: Any, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = _redact_text(str(value))
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in (
        r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+",
        r"(?i)(access[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(refresh[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(bearer[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(auth[_-]?token\s*[:=]\s*)[^\s,;]+",
        r"(?i)(authorization\s*[:=]\s*)[^\s,;]+",
        r"(?i)(password\s*[:=]\s*)[^\s,;]+",
        r"(?i)(secret\s*[:=]\s*)[^\s,;]+",
    ):
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted
