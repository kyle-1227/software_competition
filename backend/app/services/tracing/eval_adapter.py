from __future__ import annotations

from hashlib import sha256
from typing import Any

from app.services.tracing.analytics import build_trace_analytics
from app.services.tracing.serializers import resolve_capture_mode
from app.services.tracing.summary import build_trace_summary


def trace_to_eval_case(trace: Any) -> dict[str, Any]:
    summary = build_trace_summary(trace)
    analytics = build_trace_analytics(trace)
    question = str(getattr(trace, "question", "") or "")
    retrieval = summary.get("retrieval", {})
    evaluator = summary.get("evaluator", {})
    case = {
        "trace_id": getattr(trace, "trace_id", None),
        "question_hash": sha256(question.encode("utf-8")).hexdigest(),
        "question_preview": _question_preview(question),
        "failure_type": analytics.get("failure_type"),
        "root_cause_span": analytics.get("root_cause_span"),
        "retrieved_pages": retrieval.get("retrieved_pages", []),
        "evidence_count": retrieval.get("evidence_count", 0),
        "confidence": evaluator.get("confidence"),
        "suggested_fix": analytics.get("suggested_fix"),
    }
    return case


def _question_preview(question: str) -> str | None:
    if resolve_capture_mode() == "minimal":
        return None
    limit = 500 if resolve_capture_mode() == "debug" else 120
    return question if len(question) <= limit else question[:limit].rstrip() + "..."
