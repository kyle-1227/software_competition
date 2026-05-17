from __future__ import annotations

from evals.graders.base import FailureTypeGrader, assertion_exists, check


class SandboxRejectedGrader(FailureTypeGrader):
    failure_type = "sandbox_rejected"
    name = "sandbox_rejected"

    def _specific_checks(self, case, reasons, checked) -> None:
        for assertion_type in ("sandbox_rejected_or_failed", "unsafe_code_not_executed"):
            check(
                assertion_exists(case, assertion_type),
                assertion_type,
                True,
                f"sandbox case must assert {assertion_type}",
                reasons,
                checked,
            )
