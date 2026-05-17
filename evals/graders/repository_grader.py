from __future__ import annotations

from evals.graders.base import (
    FailureTypeGrader,
    assertion_exists,
    check,
    root_cause_name,
)


class TraceRepositoryFailureGrader(FailureTypeGrader):
    failure_type = "trace_repository_failure"
    name = "trace_repository_failure"

    def _specific_checks(self, case, reasons, checked) -> None:
        check(
            root_cause_name(case).startswith("trace.repository."),
            "trace_repository_root_cause",
            True,
            "repository failure must point at trace.repository.* span",
            reasons,
            checked,
        )
        check(
            assertion_exists(case, "synthetic_system_span_exists"),
            "synthetic_system_span_exists",
            True,
            "repository failure must assert synthetic system span exists",
            reasons,
            checked,
        )
