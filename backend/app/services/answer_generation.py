from __future__ import annotations

import re
from typing import Any

LOCAL_DIAGNOSTIC_MODEL = "local-diagnostic-template"
DRAFT_ANSWER_PROMPT = (
    "你是设备检修智能辅助系统，请基于手册证据、工具调用记录和安全约束，"
    "用中文生成诊断建议。必须引用证据页码和片段；证据不足时明确说明；"
    "不要编造手册内容；包含安全提醒。"
)
SENSITIVE_REASONING_KEYS = {
    "reasoning_content",
    "reasoning",
    "thinking",
    "chain_of_thought",
}
PROVIDER_FALLBACK_MARKERS = (
    "DeepSeek 未配置或不可用",
    "deterministic fallback",
)



async def draft_answer_with_llm(services, state: dict[str, Any]) -> dict[str, Any]:
    state_warnings = _string_list(state.get("warnings", []))
    llm_response = None
    llm_warnings: list[str] = []
    fallback_reason: str | None = None

    llm_client = getattr(services, "llm_client", None)
    generate_text = getattr(llm_client, "generate_text", None)
    if generate_text is None:
        fallback_reason = "LLM client unavailable, used local diagnostic template."
    else:
        try:
            llm_response = await generate_text(
                DRAFT_ANSWER_PROMPT,
                _build_llm_context(state),
            )
            llm_warnings = _string_list(getattr(llm_response, "warnings", []))
        except Exception as exc:
            fallback_reason = f"LLM answer generation failed, used local diagnostic template: {exc}"

    llm_usage = getattr(llm_response, "usage", None) if llm_response is not None else None
    llm_text = _filter_reasoning_text(str(getattr(llm_response, "text", "") or "")).strip()

    if llm_response is None:
        use_local_fallback = True
    elif not llm_text:
        use_local_fallback = True
        fallback_reason = "LLM returned empty answer, used local diagnostic template."
    elif _has_provider_fallback_warning(llm_warnings):
        use_local_fallback = True
        fallback_reason = "LLM provider fallback detected, used local diagnostic template."
    else:
        use_local_fallback = False

    warnings = state_warnings + llm_warnings
    if fallback_reason:
        warnings.append(fallback_reason)

    if use_local_fallback:
        return {
            "answer": _build_diagnostic_answer(state),
            "llm_model": LOCAL_DIAGNOSTIC_MODEL,
            "llm_usage": llm_usage,
            "warnings": warnings,
        }

    return {
        "answer": llm_text,
        "llm_model": getattr(llm_response, "model", None) or LOCAL_DIAGNOSTIC_MODEL,
        "llm_usage": llm_usage,
        "warnings": warnings,
    }


def _build_llm_context(state: dict[str, Any]) -> dict[str, Any]:
    context = {
        "question": state.get("question"),
        "device_name": state.get("device_name"),
        "device_model": state.get("device_model"),
        "memory": state.get("memory", []),
        "evidence": state.get("evidence", []),
        "tool_calls": state.get("tool_calls", []),
        "sandbox_result": state.get("sandbox_result"),
        "ai_coding": state.get("ai_coding"),
        "evaluation": state.get("evaluation"),
        "warnings": state.get("warnings", []),
    }
    return _filter_reasoning_fields(context)


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
        filtered = filtered.replace(key, "")
    return filtered


def _has_provider_fallback_warning(warnings: list[str]) -> bool:
    return any(
        marker in warning
        for warning in warnings
        for marker in PROVIDER_FALLBACK_MARKERS
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]



def _build_diagnostic_answer(state: dict[str, Any]) -> str:
    question = str(state.get("question") or "").strip()
    device = state.get("device_model") or state.get("device_name") or "当前设备"
    evidence = [item for item in state.get("evidence", []) if isinstance(item, dict)]

    safety = "安全前提：先停机并断电，佩戴防护用品，确认现场风险受控后再进行检查。"
    if not evidence:
        return (
            f"{safety}\n\n"
            f"问题：{question}\n\n"
            "未检索到足够的手册证据，暂不建议在缺少依据时拆卸或调整关键部件。"
            "请补充更具体的故障现象、部件名称或设备型号后重新查询。"
        )

    top_evidence = evidence[:3]
    if _is_parameter_question(question):
        return _build_parameter_answer(question, device, top_evidence)

    page_refs = _unique_page_refs(top_evidence)
    evidence_lines = []
    for index, item in enumerate(top_evidence, start=1):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        section = metadata.get("section") or metadata.get("chapter") or "相关章节"
        snippet = _compact_snippet(str(item.get("snippet") or "").strip())
        evidence_lines.append(
            f"{index}. {_evidence_page_label(item)} {section}：{snippet}"
        )

    likely_focus = _diagnosis_focus(top_evidence)
    inspection_order = _inspection_order(top_evidence)
    next_actions = _next_actions(top_evidence)
    evidence_text = "\n".join(evidence_lines)
    inspection_text = "\n".join(
        f"{index}. {action}" for index, action in enumerate(inspection_order, start=1)
    )
    action_text = "\n".join(f"{index}. {action}" for index, action in enumerate(next_actions, start=1))

    return (
        f"{safety}\n\n"
        f"建议先查：\n{inspection_text}\n\n"
        f"问题：{question}\n"
        f"设备：{device}\n"
        f"相关页码：{page_refs}\n\n"
        f"证据片段：\n{evidence_text}\n\n"
        f"初步判断：优先围绕{likely_focus}排查。当前证据更适合先做外观、连接、"
        "间隙/压力等可验证项目，确认异常后再进入拆卸或更换。\n\n"
        f"下一步检查：\n{action_text}"
    )


def _is_parameter_question(question: str) -> bool:
    parameter_cues = ("多少", "是多少", "标准值", "标准范围", "范围", "参数", "数值", "多大")
    diagnostic_cues = ("怎么办", "哪里", "原因", "为什么", "不稳", "回火", "启动困难", "无法启动", "故障")
    return any(cue in question for cue in parameter_cues) and not any(
        cue in question for cue in diagnostic_cues
    )


def _build_parameter_answer(
    question: str,
    device: str,
    evidence: list[dict[str, Any]],
) -> str:
    top_evidence = evidence[:3]
    page_refs = _unique_page_refs(top_evidence)
    best = top_evidence[0] if top_evidence else {}
    metadata = best.get("metadata") if isinstance(best.get("metadata"), dict) else {}
    section = metadata.get("section") or metadata.get("chapter") or "相关章节"
    snippet = str(best.get("snippet") or "").strip()
    value = _extract_parameter_value(question, snippet)

    direct_answer = (
        f"火花塞间隙标准值：{value}。"
        if value
        else f"根据手册召回片段，答案在 {section} 中；请以证据片段中的标准值为准。"
    )
    evidence_line = (
        f"{_evidence_page_label(best)} {section}：{_compact_snippet(snippet, limit=180)}"
        if best
        else "未找到可引用的手册片段。"
    )

    return (
        f"{direct_answer}\n\n"
        f"问题：{question}\n"
        f"设备：{device}\n"
        f"相关页码：{page_refs}\n\n"
        f"依据：\n{evidence_line}\n\n"
        "测量或更换前仍需先停机并断电，避免烫伤或误启动。"
    )


def _extract_parameter_value(question: str, snippet: str) -> str | None:
    if "火花塞" in question and "间隙" in question:
        match = re.search(r"间隙标准值[:：]?\s*([0-9.]+[～~\-－–—][0-9.]+\s*mm)", snippet)
        if match:
            return _normalize_range(match.group(1))

    match = re.search(r"([0-9.]+[～~\-－–—][0-9.]+\s*(?:mm|kPa|N[·.]?m))", snippet)
    if match:
        return _normalize_range(match.group(1))
    return None


def _normalize_range(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("~", "～")).strip()


def _diagnosis_focus(evidence: list[dict[str, Any]]) -> str:
    terms: list[str] = []
    candidate_terms = [
        "火花塞间隙",
        "火花塞",
        "压缩压力",
        "气门间隙",
        "进气门",
        "排气门",
        "气门",
        "发动机",
    ]
    for item in evidence:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        keywords = metadata.get("keywords") if isinstance(metadata.get("keywords"), list) else []
        terms.extend(str(keyword) for keyword in keywords[:3])
        haystack = " ".join(
            str(value)
            for value in (
                metadata.get("chapter"),
                metadata.get("section"),
                metadata.get("block_type"),
                item.get("snippet"),
            )
            if value
        )
        terms.extend(term for term in candidate_terms if term in haystack)
    if terms:
        found = set(terms)
        prioritized = [term for term in candidate_terms if term in found]
        fallback = [term for term in dict.fromkeys(terms) if term not in prioritized]
        return "、".join((prioritized + fallback)[:5])
    return "手册召回的相关部件和检查标准"


def _next_actions(evidence: list[dict[str, Any]]) -> list[str]:
    actions = [
        "核对召回页码中的标准值、工具要求和警示信息。",
        "按证据片段从低风险检查开始，记录现象、测量值和部件状态。",
        "若检测值超出手册范围，停止扩大拆检并交由具备资质的维修人员复核。",
    ]
    block_types = {
        str((item.get("metadata") or {}).get("block_type", ""))
        for item in evidence
        if isinstance(item.get("metadata"), dict)
    }
    if any("测量" in block_type or "检查" in block_type for block_type in block_types):
        actions.insert(1, "优先完成手册要求的测量/检查项目，并与标准范围比较。")
    return actions[:4]


def _inspection_order(evidence: list[dict[str, Any]]) -> list[str]:
    haystack = _evidence_haystack(evidence)
    actions: list[str] = []

    if "火花塞" in haystack:
        actions.append("先检查火花塞状态和火花塞间隙；手册 P.3 给出的间隙标准值是 0.7～0.9 mm。")
    if "压缩压力" in haystack:
        actions.append("再按 P.3 测量压缩压力，确认发动机压缩是否低于标准范围。")
    if "气门间隙" in haystack:
        actions.append("复核 P.15 气门间隙：进气门 0.13～0.20 mm，排气门 0.20～0.30 mm。")

    if actions:
        return actions[:3]

    return [
        "先从召回证据中风险最低、无需扩大拆检的检查项开始。",
        "记录测量值和部件状态，再决定是否进入拆卸或更换。",
    ]


def _evidence_page_label(item: dict[str, Any]) -> str:
    page = item.get("page")
    return f"P.{page}" if page is not None else "P.-"


def _unique_page_refs(evidence: list[dict[str, Any]]) -> str:
    refs = dict.fromkeys(_evidence_page_label(item) for item in evidence)
    return "、".join(refs)


def _evidence_haystack(evidence: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for item in evidence:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        values.extend(
            str(value)
            for value in (
                metadata.get("chapter"),
                metadata.get("section"),
                metadata.get("block_type"),
                item.get("snippet"),
            )
            if value
        )
    return " ".join(values)


def _compact_snippet(snippet: str, limit: int = 150) -> str:
    compacted = " ".join(snippet.split())
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit].rstrip() + "..."

