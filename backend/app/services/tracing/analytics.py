from __future__ import annotations

from typing import Any


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
    rules = [
        ("guardrail_blocked", _is_guardrail_blocked, "Guardrail blocked the request.", "检查输入/输出安全策略和用户请求范围。"),
        ("approval_required", _is_approval_required, "Human approval was required.", "检查高风险操作、低证据结论或 sandbox 限制。"),
        ("sandbox_rejected", _is_sandbox_rejected, "Sandbox rejected or failed execution.", "检查脚本限制、语言、超时和安全策略。"),
        ("fallback_degraded", _is_fallback_degraded, "Fallback or degraded mode was used.", "检查降级 span 和上游服务可用性。"),
        ("tool_failure", _is_tool_failure, "A tool span failed.", "检查工具 retry attempt、错误信息和依赖服务。"),
        ("retrieval_failure", _is_retrieval_failure, "Retrieval produced no effective evidence or placeholder evidence.", "检查索引、embedding、chunking、query rewrite 和 reranker。"),
        ("llm_failure", _is_llm_failure, "LLM generation failed or used local fallback.", "检查 LLM provider 配置、模型可用性和限流。"),
        ("evaluator_low_confidence", _is_evaluator_low_confidence, "Evaluator confidence was low.", "检查 evidence 覆盖、答案引用和 evaluator feedback。"),
    ]
    for failure_type, predicate, reason, suggested_fix in rules:
        span = next((item for item in spans if predicate(item)), None)
        if span is not None:
            return {
                "failure_type": failure_type,
                "root_cause_span": _span_ref(span),
                "reason": reason,
                "suggested_fix": suggested_fix,
            }
    return {
        "failure_type": "success",
        "root_cause_span": None,
        "reason": "No dominant failure signal was found.",
        "suggested_fix": "无需处理；如结果仍不符合预期，请查看 timeline 和 evidence。",
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


def _is_guardrail_blocked(span: Any) -> bool:
    name = _name(span)
    metadata = _data(span)
    return "guardrail" in name and (_is_error(span) or metadata.get("blocked") is True)


def _is_approval_required(span: Any) -> bool:
    return _name(span) == "node.approval" or bool(
        _data(span).get("requires_human_approval")
    )


def _is_sandbox_rejected(span: Any) -> bool:
    if not _name(span).startswith("sandbox."):
        return False
    data = _data(span)
    return _is_error(span) or data.get("allowed") is False or data.get("return_code") not in (None, 0)


def _is_fallback_degraded(span: Any) -> bool:
    return _span_flag(span, "degraded") or _span_flag(span, "fallback_used")


def _is_tool_failure(span: Any) -> bool:
    return _name(span).startswith("tool.") and _is_error(span)


def _is_retrieval_failure(span: Any) -> bool:
    if _name(span) != "retriever.vector_search":
        return False
    data = _data(span)
    return bool(data.get("placeholder_used")) or int(data.get("evidence_count") or 0) == 0


def _is_llm_failure(span: Any) -> bool:
    return _name(span) == "llm.answer_generation" and (
        _is_error(span) or bool(_data(span).get("local_fallback"))
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
