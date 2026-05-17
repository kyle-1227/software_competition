from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.graders.base import (
    EVAL_CASE_RESULT_SCHEMA_VERSION,
    EVAL_RUN_REPORT_SCHEMA_VERSION,
    make_report,
)
from evals.graders.registry import get_grader


def test_retrieval_failure_grader_passes_and_fails() -> None:
    passed = get_grader("retrieval_failure").grade(_case("retrieval_failure"))
    failed_case = _case("retrieval_failure")
    failed_case["assertions"] = [{"type": "failure_type", "expected": "retrieval_failure"}]
    failed = get_grader("retrieval_failure").grade(failed_case)

    assert passed.passed is True
    assert failed.passed is False


def test_tool_repository_and_success_graders() -> None:
    assert get_grader("tool_failure").grade(_case("tool_failure")).passed is True
    assert get_grader("trace_repository_failure").grade(_case("trace_repository_failure")).passed is True
    assert get_grader("success").grade(_case("success")).passed is True


def test_other_failure_graders_have_deterministic_rules() -> None:
    for failure_type in (
        "llm_failure",
        "sandbox_rejected",
        "guardrail_blocked",
        "policy_approval_required",
        "evaluator_low_confidence",
        "fallback_degraded",
    ):
        result = get_grader(failure_type).grade(_case(failure_type))
        assert result.passed is True, result.reasons


def test_unknown_failure_uses_generic_and_strict_can_fail() -> None:
    generic = get_grader("brand_new_failure")
    result = generic.grade(_case("brand_new_failure"))
    strict = generic.grade(_case("brand_new_failure"), strict=True)

    assert result.grader == "generic"
    assert result.passed is True
    assert strict.passed is True


def test_result_and_report_schema_versions_and_redaction() -> None:
    case = _case("tool_failure")
    case["metadata"] = {
        "api_key": "real-api-key",
        "answer": "FULL ANSWER SHOULD NOT LEAK " * 20,
        "reasoning": "hidden reasoning",
    }

    result = get_grader("tool_failure").grade(case)
    report = make_report(dataset_path="evals/datasets/cases.jsonl", results=[result])
    rendered = json.dumps(report.to_dict(), ensure_ascii=False)

    assert result.schema_version == EVAL_CASE_RESULT_SCHEMA_VERSION
    assert report.schema_version == EVAL_RUN_REPORT_SCHEMA_VERSION
    assert "real-api-key" not in rendered
    assert "FULL ANSWER SHOULD NOT LEAK" not in rendered
    assert "hidden reasoning" not in rendered


def _case(failure_type: str) -> dict:
    assertions = [{"type": "failure_type", "expected": failure_type}]
    case = {
        "schema_version": "trace_eval_case.v1",
        "case_id": f"case-{failure_type}",
        "trace_id": f"trace-{failure_type}",
        "failure_type": failure_type,
        "expected_behavior": "Expected behavior",
        "assertions": assertions,
        "metadata": {},
    }
    if failure_type == "retrieval_failure":
        case.update(
            {
                "evidence_count": 0,
                "expected_behavior": "System should not fabricate and should report insufficient evidence.",
                "assertions": assertions
                + [
                    {"type": "retrieval_missing_or_placeholder", "expected": True},
                    {"type": "no_fabrication_without_evidence", "expected": True},
                ],
            }
        )
    elif failure_type == "tool_failure":
        case.update(
            {
                "tool_names": ["manual_lookup"],
                "root_cause_span": {"name": "tool.manual_lookup.attempt"},
                "expected_behavior": "System should expose tool failure, retry, and avoid verified result.",
                "assertions": assertions + [{"type": "failed_tool_span_exists", "expected": True}],
            }
        )
    elif failure_type == "trace_repository_failure":
        case.update(
            {
                "root_cause_span": {"name": "trace.repository.save_span"},
                "assertions": assertions + [{"type": "synthetic_system_span_exists", "expected": True}],
            }
        )
    elif failure_type == "llm_failure":
        case["assertions"] = assertions + [{"type": "unsupported_fallback_not_grounded", "expected": True}]
    elif failure_type == "sandbox_rejected":
        case["assertions"] = assertions + [
            {"type": "sandbox_rejected_or_failed", "expected": True},
            {"type": "unsafe_code_not_executed", "expected": True},
        ]
    elif failure_type == "guardrail_blocked":
        case["guardrail_blocked"] = True
        case["assertions"] = assertions + [{"type": "blocked", "expected": True}]
    elif failure_type == "policy_approval_required":
        case["approval_required"] = True
    elif failure_type == "evaluator_low_confidence":
        case["confidence"] = 0.5
        case["assertions"] = assertions + [{"type": "confidence_below_threshold", "expected": True}]
    elif failure_type == "fallback_degraded":
        case["degraded"] = True
        case["assertions"] = assertions + [{"type": "degraded_trace", "expected": True}]
    elif failure_type == "success":
        case["expected_behavior"] = "System should preserve this successful grounded behavior."
        case["assertions"] = assertions + [{"type": "preserve_successful_grounded_behavior", "expected": True}]
    return case
