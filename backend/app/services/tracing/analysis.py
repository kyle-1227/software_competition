from __future__ import annotations

from typing import Any


def analyze_eval_case_trace(
    case_result: dict[str, Any],
    trace_summary: dict[str, Any],
) -> dict[str, Any]:
    case_id = str(case_result.get("id") or case_result.get("case_id") or "")
    summary = trace_summary if isinstance(trace_summary, dict) else {}
    retrieval = _mapping(summary.get("retrieval"))
    llm = _mapping(summary.get("llm"))
    evaluator = _mapping(summary.get("evaluator"))
    agent_loop = _mapping(summary.get("agent_loop"))
    safety = _mapping(summary.get("safety"))

    expected_pages = _int_set(case_result.get("expected_pages"))
    retrieved_pages = _int_list(
        case_result.get("retrieved_pages") or retrieval.get("retrieved_pages")
    )
    top_retrieved_pages = retrieved_pages[:3]

    signals: list[str] = []
    if expected_pages and not expected_pages.intersection(top_retrieved_pages):
        signals.append("expected page not retrieved in top 3")
        if retrieval.get("reranker_used"):
            signals.append("reranker was used but top pages missed expectation")
        return _analysis(
            case_id,
            "retrieval_miss",
            signals,
            "检查 embedding / chunking / reranker / query rewrite",
        )

    if case_result.get("placeholder_used") or retrieval.get("placeholder_used"):
        signals.append("retriever placeholder evidence was used")
        return _analysis(
            case_id,
            "retriever_placeholder_or_index_missing",
            signals,
            "检查向量索引是否构建、index_meta 是否匹配",
        )

    degraded_tools = _list_value(summary.get("degraded_tool_names"))
    if case_result.get("trace_has_degraded_tool") or degraded_tools:
        if degraded_tools:
            signals.append("degraded tools: " + ", ".join(str(name) for name in degraded_tools))
        else:
            signals.append("trace has degraded tool")
        return _analysis(
            case_id,
            "tool_degradation",
            signals,
            "检查工具调用失败原因和 retry attempt span",
        )

    if case_result.get("trace_has_local_llm_fallback") or llm.get("local_fallback"):
        signals.append("local LLM fallback was used")
        return _analysis(
            case_id,
            "llm_provider_fallback",
            signals,
            "检查 LLM provider 配置或模型可用性",
        )

    confidence = _confidence(case_result, evaluator)
    if confidence is not None and confidence < 0.7:
        signals.append(f"evaluator confidence low: {confidence:.2f}")
        return _analysis(
            case_id,
            "low_evaluation_confidence",
            signals,
            "检查 evidence 是否充分，或 evaluator feedback",
        )

    if (
        case_result.get("trace_has_approval")
        or agent_loop.get("approval_triggered")
        or safety.get("approval_triggered")
    ):
        signals.append("human approval gate was triggered")
        return _analysis(
            case_id,
            "requires_human_approval",
            signals,
            "检查高风险词、sandbox、低证据确定结论",
        )

    if (
        case_result.get("trace_has_fail_safe")
        or agent_loop.get("fail_safe_triggered")
        or safety.get("fail_safe_triggered")
    ):
        signals.append("fail-safe node was triggered")
        return _analysis(
            case_id,
            "fail_safe",
            signals,
            "检查 Agent Loop 是否达到上限或工具连续降级",
        )

    return _analysis(
        case_id,
        "unknown",
        signals or ["no dominant trace signal found"],
        "查看 trace timeline 进一步分析",
    )


def _analysis(
    case_id: str,
    likely_root_cause: str,
    signals: list[str],
    recommended_action: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "likely_root_cause": likely_root_cause,
        "signals": signals,
        "recommended_action": recommended_action,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_list(value: Any) -> list[int]:
    items = value if isinstance(value, list) else []
    result: list[int] = []
    for item in items:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _int_set(value: Any) -> set[int]:
    return set(_int_list(value))


def _confidence(case_result: dict[str, Any], evaluator: dict[str, Any]) -> float | None:
    if evaluator.get("confidence") is not None:
        return _float_or_none(evaluator.get("confidence"))
    evaluation = case_result.get("evaluation")
    if isinstance(evaluation, dict):
        return _float_or_none(evaluation.get("confidence"))
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
