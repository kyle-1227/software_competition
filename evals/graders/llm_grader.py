from __future__ import annotations

from evals.graders.base import FailureTypeGrader, assertion_exists, check


class LLMFailureGrader(FailureTypeGrader):
    failure_type = "llm_failure"
    name = "llm_failure"

    def _specific_checks(self, case, reasons, checked) -> None:
        check(
            assertion_exists(case, "unsupported_fallback_not_grounded"),
            "unsupported_fallback_not_grounded",
            True,
            "llm failure must assert unsupported fallback is not grounded",
            reasons,
            checked,
        )
        check(
            bool(case.get("expected_behavior")),
            "expected_behavior",
            True,
            "llm failure must include expected behavior",
            reasons,
            checked,
        )
