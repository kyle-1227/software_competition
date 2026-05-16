from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.tracing.analysis import analyze_eval_case_trace


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "retrieval_page_hit_at_1": retrieval_page_hit_at_k(results, 1),
        "retrieval_page_hit_at_3": retrieval_page_hit_at_k(results, 3),
        "expected_term_recall": expected_term_recall(results),
        "placeholder_rate": placeholder_rate(results),
        "answer_expected_term_hit": answer_expected_term_hit(results),
        "answer_has_citation": answer_has_citation(results),
        "avg_confidence": avg_confidence(results),
        "safety_pass_rate": safety_pass_rate(results),
        "avg_latency_ms": avg_latency_ms(results),
        "avg_trace_span_count": avg_trace_span_count(results),
        "trace_error_rate": trace_error_rate(results),
        "degraded_tool_rate": degraded_tool_rate(results),
        "local_llm_fallback_rate": local_llm_fallback_rate(results),
        "approval_trace_rate": approval_trace_rate(results),
        "fail_safe_trace_rate": fail_safe_trace_rate(results),
    }


def retrieval_page_hit_at_k(results: list[dict[str, Any]], k: int) -> float:
    scored = 0
    hits = 0
    for item in results:
        expected = {int(page) for page in item.get("expected_pages", []) if page is not None}
        if not expected:
            continue
        scored += 1
        retrieved = [
            int(page)
            for page in item.get("retrieved_pages", [])[:k]
            if page is not None
        ]
        if expected.intersection(retrieved):
            hits += 1
    return _ratio(hits, scored)


def expected_term_recall(results: list[dict[str, Any]]) -> float:
    recalls: list[float] = []
    for item in results:
        expected = [str(term) for term in item.get("expected_terms", []) if str(term)]
        if not expected:
            continue
        matched = {str(term) for term in item.get("matched_terms", [])}
        recalls.append(len([term for term in expected if term in matched]) / len(expected))
    return _avg(recalls)


def placeholder_rate(results: list[dict[str, Any]]) -> float:
    return _ratio(
        sum(1 for item in results if item.get("placeholder_used")),
        len(results),
    )


def answer_expected_term_hit(results: list[dict[str, Any]]) -> float:
    scored = 0
    hits = 0
    for item in results:
        expected = [str(term) for term in item.get("expected_terms", []) if str(term)]
        if not expected:
            continue
        scored += 1
        answer = str(item.get("answer", ""))
        if any(term in answer for term in expected):
            hits += 1
    return _ratio(hits, scored)


def answer_has_citation(results: list[dict[str, Any]]) -> float:
    citation_re = re.compile(r"(P\.?\s*\d+|第\s*\d+\s*页|page\s*\d+)", re.IGNORECASE)
    return _ratio(
        sum(1 for item in results if citation_re.search(str(item.get("answer", "")))),
        len(results),
    )


def avg_confidence(results: list[dict[str, Any]]) -> float:
    values: list[float] = []
    for item in results:
        evaluation = item.get("evaluation")
        if isinstance(evaluation, dict) and evaluation.get("confidence") is not None:
            values.append(float(evaluation["confidence"]))
    return _avg(values)


def safety_pass_rate(results: list[dict[str, Any]]) -> float:
    scored = 0
    passed = 0
    for item in results:
        evaluation = item.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("is_safe") is None:
            continue
        scored += 1
        if evaluation.get("is_safe"):
            passed += 1
    return _ratio(passed, scored)


def avg_latency_ms(results: list[dict[str, Any]]) -> float:
    return _avg(
        [
            float(item["latency_ms"])
            for item in results
            if item.get("latency_ms") is not None
        ]
    )


def avg_trace_span_count(results: list[dict[str, Any]]) -> float:
    return _avg(
        [
            float(item["trace_span_count"])
            for item in results
            if item.get("trace_span_count") is not None
        ]
    )


def trace_error_rate(results: list[dict[str, Any]]) -> float:
    return _ratio(
        sum(1 for item in results if int(item.get("trace_error_count") or 0) > 0),
        len(results),
    )


def degraded_tool_rate(results: list[dict[str, Any]]) -> float:
    return _flag_rate(results, "trace_has_degraded_tool")


def local_llm_fallback_rate(results: list[dict[str, Any]]) -> float:
    return _flag_rate(results, "trace_has_local_llm_fallback")


def approval_trace_rate(results: list[dict[str, Any]]) -> float:
    return _flag_rate(results, "trace_has_approval")


def fail_safe_trace_rate(results: list[dict[str, Any]]) -> float:
    return _flag_rate(results, "trace_has_fail_safe")


def build_comparison_report(
    old_payload: dict[str, Any],
    new_payload: dict[str, Any],
) -> str:
    old_cases = _cases_by_id(old_payload)
    new_cases = _cases_by_id(new_payload)
    old_metrics = old_payload.get("metrics") or compute_metrics(list(old_cases.values()))
    new_metrics = new_payload.get("metrics") or compute_metrics(list(new_cases.values()))

    lines = ["# Eval Comparison", "", "## Overall Metrics", ""]
    lines.extend(_metrics_table(old_metrics, new_metrics))
    lines.extend(["", "## Trace Observability Summary", ""])
    lines.extend(_trace_observability_lines(new_cases.values()))

    improvements, regressions = _case_deltas(old_cases, new_cases)
    lines.extend(["", "## Improved Cases", ""])
    lines.extend(_case_delta_lines(improvements))
    lines.extend(["", "## Regressed Cases", ""])
    lines.extend(_case_delta_lines(regressions))
    lines.extend(["", "## Failed Case Trace Analysis", ""])
    lines.extend(_failed_case_trace_analysis_lines(new_cases, regressions))
    lines.extend(["", "## Placeholder Cases", ""])
    placeholder_cases = [
        case for case in new_cases.values() if case.get("placeholder_used")
    ]
    lines.extend(_case_summary_lines(placeholder_cases))
    lines.extend(["", "## Case Summary", ""])
    lines.extend(_case_summary_table(new_cases.values()))
    return "\n".join(lines) + "\n"


def _cases_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases", payload if isinstance(payload, list) else [])
    if not isinstance(cases, list):
        return {}
    return {
        str(item.get("id")): item
        for item in cases
        if isinstance(item, dict) and item.get("id")
    }


def _metrics_table(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = sorted(set(old) | set(new))
    rows = ["| metric | old | new | delta |", "| --- | ---: | ---: | ---: |"]
    for key in keys:
        old_value = float(old.get(key, 0.0) or 0.0)
        new_value = float(new.get(key, 0.0) or 0.0)
        rows.append(
            f"| {key} | {old_value:.4f} | {new_value:.4f} | {new_value - old_value:+.4f} |"
        )
    return rows


def _case_deltas(
    old_cases: dict[str, dict[str, Any]],
    new_cases: dict[str, dict[str, Any]],
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    improvements: list[tuple[str, float]] = []
    regressions: list[tuple[str, float]] = []
    for case_id, new_case in new_cases.items():
        if case_id not in old_cases:
            continue
        delta = _case_score(new_case) - _case_score(old_cases[case_id])
        if delta > 0:
            improvements.append((case_id, delta))
        elif delta < 0:
            regressions.append((case_id, delta))
    improvements.sort(key=lambda item: item[1], reverse=True)
    regressions.sort(key=lambda item: item[1])
    return improvements[:10], regressions[:10]


def _case_score(item: dict[str, Any]) -> float:
    expected = {int(page) for page in item.get("expected_pages", []) if page is not None}
    retrieved = [
        int(page)
        for page in item.get("retrieved_pages", [])[:3]
        if page is not None
    ]
    page_hit = 1.0 if expected and expected.intersection(retrieved) else 0.0
    expected_terms = [str(term) for term in item.get("expected_terms", []) if str(term)]
    matched_terms = {str(term) for term in item.get("matched_terms", [])}
    term_score = (
        len([term for term in expected_terms if term in matched_terms]) / len(expected_terms)
        if expected_terms
        else 0.0
    )
    placeholder_penalty = 1.0 if item.get("placeholder_used") else 0.0
    return page_hit + term_score - placeholder_penalty


def _case_delta_lines(items: list[tuple[str, float]]) -> list[str]:
    if not items:
        return ["None."]
    return [f"- {case_id}: {delta:+.3f}" for case_id, delta in items]


def _case_summary_lines(cases: list[dict[str, Any]]) -> list[str]:
    if not cases:
        return ["None."]
    return [
        f"- {case.get('id')}: {case.get('question', '')}"
        for case in cases
    ]


def _case_summary_table(cases: Any) -> list[str]:
    rows = [
        "| id | hit@3 | terms | placeholder | latency_ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for case in cases:
        expected = {int(page) for page in case.get("expected_pages", []) if page is not None}
        retrieved = [
            int(page)
            for page in case.get("retrieved_pages", [])[:3]
            if page is not None
        ]
        hit = 1 if expected and expected.intersection(retrieved) else 0
        expected_terms = [str(term) for term in case.get("expected_terms", []) if str(term)]
        matched_terms = {str(term) for term in case.get("matched_terms", [])}
        term_count = len([term for term in expected_terms if term in matched_terms])
        rows.append(
            f"| {case.get('id')} | {hit} | {term_count}/{len(expected_terms)} | "
            f"{int(bool(case.get('placeholder_used')))} | {float(case.get('latency_ms', 0) or 0):.1f} |"
        )
    return rows


def _trace_observability_lines(cases: Any) -> list[str]:
    case_list = [case for case in cases if isinstance(case, dict)]
    if not case_list:
        return ["None."]
    degraded = sum(1 for case in case_list if case.get("trace_has_degraded_tool"))
    local_fallback = sum(
        1 for case in case_list if case.get("trace_has_local_llm_fallback")
    )
    approval = sum(1 for case in case_list if case.get("trace_has_approval"))
    fail_safe = sum(1 for case in case_list if case.get("trace_has_fail_safe"))
    return [
        f"- span count: {avg_trace_span_count(case_list):.2f} average",
        f"- degraded cases: {degraded}",
        f"- local fallback cases: {local_fallback}",
        f"- approval / fail-safe cases: {approval} / {fail_safe}",
    ]


def _failed_case_trace_analysis_lines(
    new_cases: dict[str, dict[str, Any]],
    regressions: list[tuple[str, float]],
) -> list[str]:
    candidates = _failed_case_candidates(new_cases, regressions)
    if not candidates:
        return ["None."]

    lines: list[str] = []
    for case in candidates[:10]:
        analysis = analyze_eval_case_trace(
            case,
            case.get("trace_summary") if isinstance(case.get("trace_summary"), dict) else {},
        )
        lines.append(f"### {case.get('id')}")
        lines.append(f"- likely root cause: {analysis.get('likely_root_cause')}")
        lines.append("- signals:")
        for signal in analysis.get("signals", [])[:5]:
            lines.append(f"  - {signal}")
        lines.append(f"- recommended action: {analysis.get('recommended_action')}")
        lines.append(f"- trace_id: {case.get('trace_id') or ''}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _failed_case_candidates(
    new_cases: dict[str, dict[str, Any]],
    regressions: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    for case in new_cases.values():
        if _is_failed_case(case):
            _append_case(ordered, seen, case)

    for case_id, _delta in regressions:
        case = new_cases.get(case_id)
        if case is not None:
            _append_case(ordered, seen, case)

    return ordered


def _is_failed_case(case: dict[str, Any]) -> bool:
    if case.get("placeholder_used"):
        return True
    expected_terms = [str(term) for term in case.get("expected_terms", []) if str(term)]
    matched_terms = {str(term) for term in case.get("matched_terms", [])}
    if expected_terms and any(term not in matched_terms for term in expected_terms):
        return True
    expected_pages = {
        int(page) for page in case.get("expected_pages", []) if page is not None
    }
    retrieved_pages = [
        int(page)
        for page in case.get("retrieved_pages", [])[:3]
        if page is not None
    ]
    if expected_pages and not expected_pages.intersection(retrieved_pages):
        return True
    confidence = _case_confidence(case)
    if confidence is not None and confidence < 0.7:
        return True
    return bool(case.get("trace_has_fail_safe") or case.get("trace_has_degraded_tool"))


def _append_case(
    ordered: list[dict[str, Any]],
    seen: set[str],
    case: dict[str, Any],
) -> None:
    case_id = str(case.get("id") or "")
    if not case_id or case_id in seen:
        return
    seen.add(case_id)
    ordered.append(case)


def _case_confidence(case: dict[str, Any]) -> float | None:
    trace_summary = case.get("trace_summary")
    if isinstance(trace_summary, dict):
        evaluator = trace_summary.get("evaluator")
        if isinstance(evaluator, dict) and evaluator.get("confidence") is not None:
            return _safe_float(evaluator.get("confidence"))
    evaluation = case.get("evaluation")
    if isinstance(evaluation, dict) and evaluation.get("confidence") is not None:
        return _safe_float(evaluation.get("confidence"))
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _flag_rate(results: list[dict[str, Any]], key: str) -> float:
    return _ratio(sum(1 for item in results if item.get(key)), len(results))
