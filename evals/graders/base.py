from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from app.services.tracing.serializers import sanitize_trace_dict

EVAL_CASE_RESULT_SCHEMA_VERSION = "trace_eval_result.v1"
EVAL_RUN_REPORT_SCHEMA_VERSION = "trace_regression_report.v1"
TRACE_EVAL_CASE_SCHEMA_VERSION = "trace_eval_case.v1"


@dataclass
class GradeOutcome:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    assertions_checked: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvalCaseResult:
    case_id: str
    trace_id: str
    failure_type: str
    passed: bool
    score: float
    grader: str
    reasons: list[str]
    assertions_checked: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = EVAL_CASE_RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return sanitize_report_dict(asdict(self))


@dataclass
class EvalRunReport:
    run_id: str
    dataset_path: str
    created_at: str
    total_cases: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float
    average_score: float
    results: list[EvalCaseResult]
    schema_version: str = EVAL_RUN_REPORT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return sanitize_report_dict(payload)


class TraceCaseGrader(Protocol):
    failure_type: str
    name: str

    def grade(self, case: dict[str, Any], *, strict: bool = False) -> EvalCaseResult: ...


class GenericGrader:
    failure_type = "*"
    name = "generic"

    def grade(self, case: dict[str, Any], *, strict: bool = False) -> EvalCaseResult:
        outcome = self._grade(case, strict=strict)
        return EvalCaseResult(
            case_id=str(case.get("case_id") or ""),
            trace_id=str(case.get("trace_id") or ""),
            failure_type=str(case.get("failure_type") or "unknown_failure"),
            passed=outcome.passed,
            score=outcome.score,
            grader=self.name,
            reasons=outcome.reasons,
            assertions_checked=outcome.assertions_checked,
            metadata={"strict": strict} if strict else {},
        )

    def _grade(self, case: dict[str, Any], *, strict: bool = False) -> GradeOutcome:
        del strict
        reasons: list[str] = []
        checked: list[dict[str, Any]] = []
        _require(case.get("schema_version") == TRACE_EVAL_CASE_SCHEMA_VERSION, "schema_version", reasons)
        _require(bool(case.get("case_id")), "case_id is required", reasons)
        _require(bool(case.get("trace_id")), "trace_id is required", reasons)
        _require(bool(case.get("failure_type")), "failure_type is required", reasons)
        _require(bool(case.get("expected_behavior")), "expected_behavior is required", reasons)
        assertions = _assertions(case)
        _require(bool(assertions), "assertions are required", reasons)
        for assertion in assertions:
            has_shape = "type" in assertion and "expected" in assertion
            checked.append(
                {
                    "type": str(assertion.get("type") or "unknown"),
                    "expected": assertion.get("expected"),
                    "passed": has_shape,
                }
            )
            _require(has_shape, "assertion must include type and expected", reasons)
        _require(not contains_sensitive_or_full_content(case), "case contains unsafe content", reasons)
        return _outcome(reasons, checked)


class FailureTypeGrader(GenericGrader):
    failure_type = ""
    name = ""

    def _grade(self, case: dict[str, Any], *, strict: bool = False) -> GradeOutcome:
        base = super()._grade(case, strict=strict)
        reasons = list(base.reasons)
        checked = list(base.assertions_checked)
        _require(
            case.get("failure_type") == self.failure_type,
            f"failure_type must be {self.failure_type}",
            reasons,
        )
        checked.append(
            {
                "type": "failure_type",
                "expected": self.failure_type,
                "passed": case.get("failure_type") == self.failure_type,
            }
        )
        self._specific_checks(case, reasons, checked)
        return _outcome(reasons, checked)

    def _specific_checks(
        self,
        case: dict[str, Any],
        reasons: list[str],
        checked: list[dict[str, Any]],
    ) -> None:
        del case, reasons, checked


def make_report(
    *,
    dataset_path: str,
    results: list[EvalCaseResult],
    skipped: int = 0,
    empty_dataset_passes: bool = True,
) -> EvalRunReport:
    passed = sum(1 for result in results if result.passed)
    failed = sum(1 for result in results if not result.passed)
    total = len(results) + skipped
    if not results and skipped == 0:
        average_score = 1.0 if empty_dataset_passes else 0.0
        pass_rate = 1.0 if empty_dataset_passes else 0.0
    else:
        average_score = (
            round(sum(result.score for result in results) / len(results), 4)
            if results
            else 0.0
        )
        pass_rate = round(passed / len(results), 4) if results else 0.0
    return EvalRunReport(
        run_id=uuid4().hex,
        dataset_path=dataset_path,
        created_at=datetime.now(timezone.utc).isoformat(),
        total_cases=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        pass_rate=pass_rate,
        average_score=average_score,
        results=results,
    )


def assertion_exists(case: dict[str, Any], assertion_type: str) -> bool:
    return any(assertion.get("type") == assertion_type for assertion in _assertions(case))


def check(
    condition: bool,
    assertion_type: str,
    expected: Any,
    reason: str,
    reasons: list[str],
    checked: list[dict[str, Any]],
) -> None:
    checked.append({"type": assertion_type, "expected": expected, "passed": bool(condition)})
    _require(condition, reason, reasons)


def root_cause_name(case: dict[str, Any]) -> str:
    span = case.get("root_cause_span")
    return str(span.get("name") or "") if isinstance(span, dict) else ""


def expected_behavior(case: dict[str, Any]) -> str:
    return str(case.get("expected_behavior") or "")


def sanitize_report_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_trace_dict(payload)


def contains_sensitive_or_full_content(value: Any) -> bool:
    rendered = json.dumps(value, ensure_ascii=False, default=str).lower()
    forbidden_tokens = (
        "api_key=",
        "access_token=",
        "refresh_token=",
        "authorization: bearer",
        "authorization=bearer",
        "password=",
        "secret=",
        "chain_of_thought",
        "reasoning_content",
        "hidden reasoning",
        "hidden chain",
        "full question should not leak",
        "full answer should not leak",
        "full script should not leak",
        "full evidence should not leak",
    )
    return any(token in rendered for token in forbidden_tokens)


def _assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    assertions = case.get("assertions")
    if not isinstance(assertions, list):
        return []
    return [item for item in assertions if isinstance(item, dict)]


def _require(condition: bool, reason: str, reasons: list[str]) -> None:
    if not condition:
        reasons.append(reason)


def _outcome(reasons: list[str], checked: list[dict[str, Any]]) -> GradeOutcome:
    passed = not reasons
    if passed:
        score = 1.0
    else:
        total = max(1, len(checked) + len(reasons))
        score = round(max(0.0, (total - len(reasons)) / total), 4)
    return GradeOutcome(
        passed=passed,
        score=score,
        reasons=reasons,
        assertions_checked=checked,
    )
