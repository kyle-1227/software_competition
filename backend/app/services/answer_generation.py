from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.trace import SpanKind
from app.services.tracing.context import trace_span

LOCAL_DIAGNOSTIC_MODEL = "evidence-bound-template"
INSUFFICIENT_EVIDENCE_ANSWER = (
    "当前知识库没有检索到足够证据，不能给出确定维修结论。"
    "请补充设备型号、故障现象或上传对应手册。"
)
SENSITIVE_REASONING_KEYS = {
    "reasoning_content",
    "reasoning",
    "thinking",
    "chain_of_thought",
}


async def draft_answer_with_llm(services, state: dict[str, Any]) -> dict[str, Any]:
    """Generate an evidence-bound draft answer through the model gateway."""

    trace_store = getattr(services, "trace_store", None)
    trace_id = state.get("trace_id")
    async with trace_span(
        trace_store,
        trace_id,
        "llm.answer_generation",
        SpanKind.LLM,
        inputs={
            "question": state.get("question"),
            "evidence_count": len(state.get("evidence", []) or []),
            "tool_call_count": len(state.get("tool_calls", []) or []),
        },
        metadata={"mode": "model_gateway"},
    ) as span:
        evidence = _verified_evidence(state.get("evidence", []))
        warnings = _string_list(state.get("warnings", []))
        if not evidence:
            result = {
                "answer": "",
                "llm_model": None,
                "llm_usage": None,
                "warnings": warnings + ["insufficient evidence_id-backed evidence"],
                "llm_generation_failed": True,
                "safe_fallback_available": False,
            }
        else:
            result = await _generate_with_model(services, state, evidence, warnings)
        span.set_metadata(
            {
                "verified_evidence_count": len(evidence),
                "answer_length": len(result["answer"]),
                "insufficient_evidence": not evidence,
                "llm_generation_failed": bool(result.get("llm_generation_failed")),
                "llm_model": result.get("llm_model"),
            }
        )
        span.set_outputs(
            {
                "answer_length": len(result["answer"]),
                "verified_evidence_count": len(evidence),
            }
        )
        return result


async def _generate_with_model(
    services: Any,
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    llm = getattr(services, "model_gateway", None) or getattr(services, "llm_client", None)
    if llm is None:
        return {
            "answer": "",
            "llm_model": None,
            "llm_usage": None,
            "warnings": warnings + ["answer generation model unavailable"],
            "llm_generation_failed": True,
            "safe_fallback_available": False,
        }

    context = {
        "question": state.get("question"),
        "device_name": state.get("device_name"),
        "device_model": state.get("device_model"),
        "evidence": evidence[:8],
        "tool_calls": _compact_tool_calls(state.get("tool_calls", [])),
        "sop": _string_list(state.get("sop", []))[:8],
        "previous_answer": state.get("previous_answer") or state.get("answer") or "",
        "evaluation_feedback": state.get("evaluation_feedback") or "",
        "iteration": int(state.get("generation_iteration", 1) or 1),
    }
    prompt = _draft_prompt()
    try:
        try:
            response = await llm.generate_text(
                prompt,
                context,
                task="answer_generation",
            )
        except TypeError:
            response = await llm.generate_text(prompt, context)
    except Exception as exc:
        return {
            "answer": "",
            "llm_model": None,
            "llm_usage": None,
            "warnings": warnings + [f"answer generation model failed: {exc}"],
            "llm_generation_failed": True,
            "safe_fallback_available": False,
        }

    text = str(getattr(response, "text", "") or "").strip()
    response_warnings = _string_list(getattr(response, "warnings", []))
    if not text or _is_fallback_response(response, text, response_warnings):
        return {
            "answer": "",
            "llm_model": getattr(response, "model", None),
            "llm_usage": getattr(response, "usage", None),
            "warnings": warnings + response_warnings + ["answer generation used fallback"],
            "llm_generation_failed": True,
            "safe_fallback_available": False,
        }

    return {
        "answer": _filter_reasoning_text(text),
        "llm_model": getattr(response, "model", None),
        "llm_usage": getattr(response, "usage", None),
        "warnings": warnings + response_warnings,
        "llm_generation_failed": False,
        "safe_fallback_available": False,
    }


def _draft_prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "prompts" / "draft_answer_prompt.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return "Write an evidence-bound maintenance answer. Do not invent manual content."


def _is_fallback_response(response: Any, text: str, warnings: list[str]) -> bool:
    provider = str(getattr(response, "provider", "") or "").lower()
    warning_text = " ".join(warnings).lower()
    lowered = text.lower()
    return (
        provider == "fallback"
        or "fallback" in warning_text
        or "deterministic fallback" in lowered
    )


def _build_evidence_summary(
    state: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    question = str(state.get("question") or "").strip()
    device = state.get("device_model") or state.get("device_name") or "当前设备"
    lines = [
        "以下内容仅基于当前检索到的证据片段，不包含证据外的确定性维修结论。",
        f"问题：{question}",
        f"设备：{device}",
        "",
        "证据摘要：",
    ]
    for index, item in enumerate(evidence[:5], start=1):
        evidence_id = _evidence_id(item)
        source = str(item.get("source") or "unknown")
        page = item.get("page")
        page_text = f"，页码：{page}" if page is not None else ""
        snippet = _compact_snippet(str(item.get("snippet") or ""))
        lines.append(
            f"{index}. evidence_id={evidence_id}，来源：{source}{page_text}。片段：{snippet}"
        )
    lines.extend(
        [
            "",
            "结论边界：只能依据以上 evidence_id 对应片段继续分析；如需更确定的维修结论，请补充更多手册证据或设备型号。",
        ]
    )
    return "\n".join(lines)


def _verified_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if not _evidence_id(item) or _is_placeholder(item):
            continue
        if not str(item.get("snippet") or "").strip():
            continue
        result.append(_filter_reasoning_fields(item))
    return result


def _evidence_id(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return str(item.get("evidence_id") or metadata.get("evidence_id") or "")


def _is_placeholder(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    retriever = str(metadata.get("retriever") or "").lower()
    source = str(item.get("source") or "").lower()
    return (
        bool(item.get("is_placeholder"))
        or "placeholder" in retriever
        or "placeholder" in source
        or retriever == "manual_lookup-degraded"
    )


def _filter_reasoning_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _filter_reasoning_fields(item)
            for key, item in value.items()
            if str(key) not in SENSITIVE_REASONING_KEYS
        }
    if isinstance(value, list):
        return [_filter_reasoning_fields(item) for item in value]
    return value


def _filter_reasoning_text(text: str) -> str:
    filtered = text
    for key in SENSITIVE_REASONING_KEYS:
        filtered = filtered.replace(str(key), "")
    return filtered.strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _compact_snippet(snippet: str, limit: int = 220) -> str:
    compacted = " ".join(snippet.split())
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit].rstrip() + "..."


def _compact_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in value[-8:]:
        if not isinstance(item, dict):
            continue
        calls.append(
            {
                "tool_name": item.get("tool_name"),
                "status": item.get("status"),
                "output": item.get("output"),
            }
        )
    return calls
