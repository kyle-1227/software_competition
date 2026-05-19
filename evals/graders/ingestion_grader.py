from __future__ import annotations

from evals.graders.harness_base import HarnessGradeResult, require, score_from_reasons


class IngestionGrader:
    name = "ingestion"

    def grade(self, case: dict, observed: dict | None = None) -> HarnessGradeResult:
        observed = observed or case.get("observed") or {}
        expected_fields = case.get("expected_fields") or []
        payload = observed or case
        reasons: list[str] = []
        require(bool(case.get("id")), "id is required", reasons)
        require(isinstance(expected_fields, list) and bool(expected_fields), "expected_fields are required", reasons)
        for field in expected_fields:
            require(field in payload, f"missing expected ingestion field: {field}", reasons)
        return HarnessGradeResult(
            case_id=str(case.get("id") or ""),
            grader=self.name,
            passed=not reasons,
            score=score_from_reasons(reasons),
            reasons=reasons,
        )
