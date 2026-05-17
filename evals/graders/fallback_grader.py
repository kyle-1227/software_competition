from __future__ import annotations

from evals.graders.base import FailureTypeGrader, assertion_exists, check


class FallbackDegradedGrader(FailureTypeGrader):
    failure_type = "fallback_degraded"
    name = "fallback_degraded"

    def _specific_checks(self, case, reasons, checked) -> None:
        has_flag = bool(case.get("degraded")) or bool(case.get("fallback_used"))
        has_assertion = assertion_exists(case, "degraded_trace") or assertion_exists(case, "fallback_used")
        check(
            has_flag,
            "degraded_or_fallback_used",
            True,
            "fallback case must mark degraded or fallback_used",
            reasons,
            checked,
        )
        check(
            has_assertion,
            "degraded_or_fallback_assertion",
            True,
            "fallback case must assert degraded or fallback_used",
            reasons,
            checked,
        )
