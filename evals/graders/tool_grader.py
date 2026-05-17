from __future__ import annotations

from evals.graders.base import (
    FailureTypeGrader,
    assertion_exists,
    check,
    expected_behavior,
    root_cause_name,
)


class ToolFailureGrader(FailureTypeGrader):
    failure_type = "tool_failure"
    name = "tool_failure"

    def _specific_checks(self, case, reasons, checked) -> None:
        has_tool_signal = bool(case.get("tool_names")) or root_cause_name(case).startswith("tool.")
        check(
            has_tool_signal,
            "tool_failure_signal",
            True,
            "tool failure must include tool names or a tool root cause span",
            reasons,
            checked,
        )
        check(
            assertion_exists(case, "failed_tool_span_exists"),
            "failed_tool_span_exists",
            True,
            "tool failure must assert failed tool span exists",
            reasons,
            checked,
        )
        behavior = expected_behavior(case).lower()
        check(
            "tool failure" in behavior and ("retry" in behavior or "verified" in behavior),
            "expected_behavior_mentions_tool_failure",
            True,
            "expected behavior must describe tool failure handling",
            reasons,
            checked,
        )
