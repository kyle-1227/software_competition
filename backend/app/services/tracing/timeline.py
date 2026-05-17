from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.tracing.summary import build_trace_summary


def build_trace_timeline(trace: Any, *, include_summary: bool = True) -> str:
    if trace is None:
        return "Trace not found."

    spans = [
        span
        for span in _walk_spans(getattr(trace, "root_span", None))
        if span is not getattr(trace, "root_span", None)
    ]
    spans.sort(key=lambda span: _sort_time(getattr(span, "start_time", None)))

    lines = ["# Trace Timeline", ""]
    if include_summary:
        summary = build_trace_summary(trace)
        retrieval = summary.get("retrieval", {})
        llm = summary.get("llm", {})
        evaluator = summary.get("evaluator", {})
        safety = summary.get("safety", {})
        lines.extend(
            [
                "## Summary",
                f"- Trace ID: {summary.get('trace_id') or ''}",
                f"- Total spans: {summary.get('span_count', 0)}",
                f"- Errors: {summary.get('error_count', 0)}",
                f"- Retrieved pages: {_format_list(retrieval.get('retrieved_pages'))}",
                f"- LLM fallback: {_yes_no(llm.get('fallback_used'))}",
                f"- Evaluator confidence: {_format_optional(evaluator.get('confidence'))}",
                f"- Approval triggered: {_yes_no(safety.get('approval_triggered'))}",
                f"- Fail-safe triggered: {_yes_no(safety.get('fail_safe_triggered'))}",
                "",
            ]
        )

    lines.extend(["## Timeline"])
    if not spans:
        lines.append("No spans recorded.")
        return "\n".join(lines) + "\n"

    for index, span in enumerate(spans, start=1):
        name = str(getattr(span, "name", "") or "")
        status = str(getattr(getattr(span, "status", None), "value", getattr(span, "status", "")) or "")
        duration = _duration_ms(span)
        duration_text = f"{duration:.1f}ms" if duration is not None else "n/a"
        lines.append(f"{index}. {name} - {status.upper()} - {duration_text}")
        for key, value in _span_details(span):
            lines.append(f"   - {key}: {_format_value(value)}")
    return "\n".join(lines) + "\n"


def _span_details(span: Any) -> list[tuple[str, Any]]:
    name = str(getattr(span, "name", "") or "")
    metadata = _mapping(getattr(span, "metadata", None))
    outputs = _mapping(getattr(span, "outputs", None))
    data = {**metadata, **outputs}

    if name.startswith("tool.") and name.endswith(".attempt"):
        return _pick(
            data,
            ("attempt", "max_retries", "success", "will_retry"),
            aliases={"attempt": "attempt", "max_retries": "max_retries"},
        )
    if name == "sandbox.execute.attempt":
        return _pick(
            data,
            ("attempt", "max_retries", "success", "allowed", "return_code", "will_retry"),
        )
    if name == "sandbox.execute.skipped":
        return _pick(data, ("reason", "requires_human_approval"))
    if name == "retriever.vector_search":
        return _pick(
            data,
            ("retrieved_pages", "evidence_count", "fallback_used", "placeholder_used"),
        )
    if name == "retriever.query_rewrite":
        return _pick(
            data,
            ("hyde_enabled", "rewriter_model", "fallback_used", "query_length", "rewritten_query_length"),
        )
    if name == "reranker.score":
        return _pick(
            data,
            ("candidate_count", "selected_count", "top_n", "fallback_used"),
        )
    if name == "llm.answer_generation":
        return _pick(
            data,
            ("llm_model", "model", "fallback_used", "local_fallback", "answer_length"),
        )
    if name == "evaluator.optimizer":
        return _pick(
            data,
            ("final_confidence", "confidence", "iteration_count", "issues_count"),
        )
    if name in {"node.loop_decision", "node.post_eval_loop_decision"}:
        return _pick(data, ("decision_action", "action", "route", "decision"))
    if name in {"node.approval", "node.fail_safe", "node.clarification"}:
        return [("triggered", True)]
    if name.startswith("worker."):
        return _pick(data, ("evidence_count", "tool_call_count", "degraded"))
    if name == "node.orchestrator":
        return _pick(data, ("intent", "workers", "priority"))
    return []


def _pick(
    data: dict[str, Any],
    keys: tuple[str, ...],
    *,
    aliases: dict[str, str] | None = None,
) -> list[tuple[str, Any]]:
    aliases = aliases or {}
    pairs: list[tuple[str, Any]] = []
    for key in keys:
        if key in data and data[key] is not None:
            pairs.append((aliases.get(key, key), data[key]))
    return pairs


def _walk_spans(span: Any):
    if span is None:
        return
    yield span
    for child in getattr(span, "children", []) or []:
        yield from _walk_spans(child)


def _duration_ms(span: Any) -> float | None:
    attr_value = _float_or_none(getattr(span, "duration_ms", None))
    if attr_value is not None:
        return attr_value
    metadata = _mapping(getattr(span, "metadata", None))
    value = _float_or_none(metadata.get("duration_ms"))
    if value is not None:
        return value
    start = getattr(span, "start_time", None)
    end = getattr(span, "end_time", None)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    return (end - start).total_seconds() * 1000


def _sort_time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.max


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_list(value: Any) -> str:
    items = value if isinstance(value, list) else []
    return ", ".join(str(item) for item in items) if items else "none"


def _format_optional(value: Any) -> str:
    return str(value) if value is not None else "n/a"


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_redact_text(str(item)) for item in value[:20]) + "]"
    if isinstance(value, bool):
        return str(value).lower()
    return _redact_text(str(value))


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
