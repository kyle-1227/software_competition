from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_trace_regression import main, run_trace_regression


def test_runner_reads_dataset_and_writes_versioned_report(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    report = tmp_path / "report.json"
    dataset.write_text(json.dumps(_case("retrieval_failure")) + "\n", encoding="utf-8")

    code = main(["--dataset", str(dataset), "--report", str(report), "--json"])

    data = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert data["schema_version"] == "trace_regression_report.v1"
    assert data["results"][0]["schema_version"] == "trace_eval_result.v1"
    assert data["passed"] == 1


def test_runner_filters_limit_and_case_id(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        "\n".join(
            [
                json.dumps(_case("retrieval_failure", case_id="case-a")),
                json.dumps(_case("tool_failure", case_id="case-b")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    by_type = run_trace_regression(dataset=dataset, failure_type="tool_failure")
    by_case = run_trace_regression(dataset=dataset, case_id="case-a")
    limited = run_trace_regression(dataset=dataset, limit=1)

    assert by_type.total_cases == 2
    assert by_type.skipped == 1
    assert by_type.results[0].failure_type == "tool_failure"
    assert by_case.results[0].case_id == "case-a"
    assert limited.skipped == 1


def test_fail_under_sets_nonzero_exit_code(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    broken = _case("retrieval_failure")
    broken["assertions"] = [{"type": "failure_type", "expected": "retrieval_failure"}]
    dataset.write_text(json.dumps(broken) + "\n", encoding="utf-8")

    code = main(["--dataset", str(dataset), "--report", str(tmp_path / "r.json"), "--fail-under", "0.9"])

    assert code == 1


def test_strict_unknown_failure_fails(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps(_case("unknown_new_failure")) + "\n", encoding="utf-8")

    report = run_trace_regression(dataset=dataset, strict=True)

    assert report.failed == 1
    assert report.results[0].passed is False


def test_report_redacts_sensitive_content(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    case = _case("tool_failure")
    case["metadata"] = {
        "api_key": "real-api-key",
        "script": "FULL SCRIPT SHOULD NOT LEAK " * 20,
        "reasoning": "hidden reasoning",
    }
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    main(["--dataset", str(dataset), "--report", str(report_path)])
    rendered = report_path.read_text(encoding="utf-8")

    assert "real-api-key" not in rendered
    assert "FULL SCRIPT SHOULD NOT LEAK" not in rendered
    assert "hidden reasoning" not in rendered


def test_gitignore_has_generated_eval_artifact_policy() -> None:
    gitignore = open(".gitignore", encoding="utf-8").read()

    assert "evals/reports/*.json" in gitignore
    assert "evals/reports/*.jsonl" in gitignore
    assert "evals/reports/*.md" in gitignore
    assert "evals/datasets/trace_regression_cases.jsonl" in gitignore


def _case(failure_type: str, *, case_id: str | None = None) -> dict:
    assertions = [{"type": "failure_type", "expected": failure_type}]
    case = {
        "schema_version": "trace_eval_case.v1",
        "case_id": case_id or f"case-{failure_type}",
        "trace_id": f"trace-{failure_type}",
        "failure_type": failure_type,
        "expected_behavior": "Expected behavior",
        "assertions": assertions,
    }
    if failure_type == "retrieval_failure":
        case.update(
            {
                "evidence_count": 0,
                "expected_behavior": "System should not fabricate and report insufficient evidence.",
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
    return case
