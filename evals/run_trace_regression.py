from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from evals.graders.base import EvalRunReport, make_report  # noqa: E402
from evals.graders.registry import get_grader, is_known_failure_type  # noqa: E402

DEFAULT_DATASET = ROOT / "evals" / "datasets" / "trace_regression_cases.jsonl"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "trace_regression_report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic trace regression evals.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--failure-type")
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fail-under", type=float, default=0.0)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    report = run_trace_regression(
        dataset=args.dataset,
        failure_type=args.failure_type,
        case_id=args.case_id,
        limit=args.limit,
        strict=args.strict,
    )
    write_report(report, args.report)
    summary = _summary(report, str(args.report))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(_summary_text(summary))
    return 1 if report.pass_rate < args.fail_under else 0


def run_trace_regression(
    *,
    dataset: Path = DEFAULT_DATASET,
    failure_type: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
    strict: bool = False,
) -> EvalRunReport:
    skipped = 0
    results = []
    selected = 0
    for case in load_cases(dataset):
        if failure_type and case.get("failure_type") != failure_type:
            skipped += 1
            continue
        if case_id and case.get("case_id") != case_id:
            skipped += 1
            continue
        if limit is not None and selected >= max(0, int(limit)):
            skipped += 1
            continue
        selected += 1
        grader = get_grader(str(case.get("failure_type") or ""))
        result = grader.grade(case, strict=strict)
        if strict and not is_known_failure_type(str(case.get("failure_type") or "")):
            result.passed = False
            result.score = 0.0
            result.reasons.append("unknown failure type is not allowed in strict mode")
        results.append(result)
    if strict and skipped:
        for index in range(skipped):
            results.append(
                get_grader("").grade(
                    {
                        "schema_version": "trace_eval_case.v1",
                        "case_id": f"skipped-{index}",
                        "trace_id": "",
                        "failure_type": "unknown_failure",
                        "expected_behavior": "skipped case",
                        "assertions": [{"type": "skipped", "expected": False}],
                    },
                    strict=True,
                )
            )
            results[-1].passed = False
            results[-1].score = 0.0
            results[-1].reasons.append("skipped case is not allowed in strict mode")
        skipped = 0
    return make_report(dataset_path=str(dataset), results=results, skipped=skipped)


def load_cases(dataset: Path) -> list[dict[str, Any]]:
    if not Path(dataset).exists():
        return []
    cases: list[dict[str, Any]] = []
    with Path(dataset).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                cases.append(payload)
    return cases


def write_report(report: EvalRunReport, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _summary(report: EvalRunReport, report_path: str) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "dataset_path": report.dataset_path,
        "total_cases": report.total_cases,
        "passed": report.passed,
        "failed": report.failed,
        "skipped": report.skipped,
        "pass_rate": report.pass_rate,
        "average_score": report.average_score,
        "report": report_path,
    }


def _summary_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Trace Regression Eval",
            f"Dataset: {summary['dataset_path']}",
            f"Total: {summary['total_cases']}",
            f"Passed: {summary['passed']}",
            f"Failed: {summary['failed']}",
            f"Skipped: {summary['skipped']}",
            f"Pass rate: {summary['pass_rate'] * 100:.2f}%",
            f"Average score: {summary['average_score']:.2f}",
            f"Report: {summary['report']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
