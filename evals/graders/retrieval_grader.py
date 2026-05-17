from __future__ import annotations

from evals.graders.base import (
    FailureTypeGrader,
    assertion_exists,
    check,
    expected_behavior,
)


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
