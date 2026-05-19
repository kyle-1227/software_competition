from __future__ import annotations

from evals.graders.base import (
    FailureTypeGrader,
    assertion_exists,
    check,
    expected_behavior,
)
from evals.graders.harness_base import HarnessGradeResult, require, score_from_reasons


class RetrievalFailureGrader(FailureTypeGrader):
    failure_type = "retrieval_failure"
    name = "retrieval_failure"

    def _specific_checks(self, case, reasons, checked) -> None:
        missing_or_placeholder = int(case.get("evidence_count") or 0) == 0 or assertion_exists(
            case, "retrieval_missing_or_placeholder"
        )
        check(
            missing_or_placeholder,
            "retrieval_missing_or_placeholder",
            True,
            "retrieval failure must indicate missing or placeholder evidence",
            reasons,
            checked,
        )
        check(
            assertion_exists(case, "no_fabrication_without_evidence"),
            "no_fabrication_without_evidence",
            True,
            "retrieval failure must assert no fabrication without evidence",
            reasons,
            checked,
        )
        behavior = expected_behavior(case).lower()
        check(
            "insufficient evidence" in behavior or "not fabricate" in behavior,
            "expected_behavior_mentions_evidence_or_fabrication",
            True,
            "expected behavior must mention insufficient evidence or no fabrication",
            reasons,
            checked,
        )


class RetrievalCaseGrader:
    name = "retrieval"

    def grade(self, case: dict, observed: dict | None = None) -> HarnessGradeResult:
        observed = observed or {}
        reasons: list[str] = []
        require(bool(case.get("id")), "id is required", reasons)
        require(bool(case.get("query")), "query is required", reasons)
        expected_keywords = case.get("expected_keywords")
        require(isinstance(expected_keywords, list) and bool(expected_keywords), "expected_keywords are required", reasons)
        expected_page = case.get("expected_page")
        observed_text = _observed_text(observed) or str(case.get("query") or "")
        for keyword in expected_keywords or []:
            require(str(keyword) in observed_text, f"missing expected keyword: {keyword}", reasons)
        if expected_page is not None:
            pages = observed.get("pages") if isinstance(observed.get("pages"), list) else []
            require(expected_page in pages, f"missing expected page: {expected_page}", reasons)
        return HarnessGradeResult(
            case_id=str(case.get("id") or ""),
            grader=self.name,
            passed=not reasons,
            score=score_from_reasons(reasons),
            reasons=reasons,
            metadata={"expected_page": expected_page},
        )


def _observed_text(observed: dict) -> str:
    values: list[str] = []
    for item in observed.get("evidence", []) if isinstance(observed.get("evidence"), list) else []:
        if isinstance(item, dict):
            values.append(str(item.get("snippet") or ""))
            values.append(str(item.get("source") or ""))
    return " ".join(values)
