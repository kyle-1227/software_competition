from __future__ import annotations

from evals.graders.base import FailureTypeGrader, assertion_exists, check, expected_behavior


class SuccessGrader(FailureTypeGrader):
    failure_type = "success"
    name = "success"

    def _specific_checks(self, case, reasons, checked) -> None:
        check(
            expected_behavior(case) == "System should preserve this successful grounded behavior.",
            "expected_behavior_success",
            True,
            "success case must use stable success expected behavior",
            reasons,
            checked,
        )
        check(
            assertion_exists(case, "preserve_successful_grounded_behavior"),
            "preserve_successful_grounded_behavior",
            True,
            "success case must assert preservation of grounded behavior",
            reasons,
            checked,
        )
