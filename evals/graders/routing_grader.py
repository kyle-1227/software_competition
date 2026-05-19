from __future__ import annotations

from evals.graders.harness_base import HarnessGradeResult, require, score_from_reasons


class RoutingGrader:
    name = "tool_routing"

    def grade(self, case: dict, observed: dict | None = None) -> HarnessGradeResult:
        observed = observed or case.get("observed") or {}
        expected_tools = case.get("expected_tools") or []
        actual_tools = observed.get("tool_calls") or case.get("tool_calls") or []
        actual_names = [
            str(item.get("tool_name"))
            for item in actual_tools
            if isinstance(item, dict) and item.get("tool_name")
        ]
        reasons: list[str] = []
        require(bool(case.get("id")), "id is required", reasons)
        require(isinstance(expected_tools, list) and bool(expected_tools), "expected_tools are required", reasons)
        for tool_name in expected_tools:
            require(str(tool_name) in actual_names or str(tool_name) in str(case.get("query") or ""), f"missing expected tool: {tool_name}", reasons)
        return HarnessGradeResult(
            case_id=str(case.get("id") or ""),
            grader=self.name,
            passed=not reasons,
            score=score_from_reasons(reasons),
            reasons=reasons,
        )
