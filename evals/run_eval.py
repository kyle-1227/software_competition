from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from evals.graders.groundedness_grader import GroundednessGrader  # noqa: E402
from evals.graders.ingestion_grader import IngestionGrader  # noqa: E402
from evals.graders.retrieval_grader import RetrievalCaseGrader  # noqa: E402
from evals.graders.routing_grader import RoutingGrader  # noqa: E402
from evals.graders.safety_grader import SafetyGrader  # noqa: E402

DATASETS = {
    "retrieval": ROOT / "evals" / "datasets" / "retrieval_cases.jsonl",
    "groundedness": ROOT / "evals" / "datasets" / "groundedness_cases.jsonl",
    "safety": ROOT / "evals" / "datasets" / "safety_cases.jsonl",
    "tool_routing": ROOT / "evals" / "datasets" / "tool_routing_cases.jsonl",
    "ingestion": ROOT / "evals" / "datasets" / "ingestion_cases.jsonl",
}
GRADERS = {
    "retrieval": RetrievalCaseGrader(),
    "groundedness": GroundednessGrader(),
    "safety": SafetyGrader(),
    "tool_routing": RoutingGrader(),
    "ingestion": IngestionGrader(),
}
DEFAULT_REPORT = ROOT / "evals" / "reports" / "harness_eval_report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Harness evals.")
    parser.add_argument("--suite", choices=[*DATASETS.keys(), "all"], default="all")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--fail-under", type=float, default=1.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run_eval(suite=args.suite)
    write_report(report, args.report)
    summary = {
        "schema_version": report["schema_version"],
        "total_cases": report["total_cases"],
        "passed": report["passed"],
        "failed": report["failed"],
        "pass_rate": report["pass_rate"],
        "report": str(args.report),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(_summary_text(summary))
    return 1 if report["pass_rate"] < args.fail_under else 0


def run_eval(*, suite: str = "all") -> dict[str, Any]:
    selected = DATASETS.keys() if suite == "all" else [suite]
    results = []
    for name in selected:
        grader = GRADERS[name]
        for case in load_cases(DATASETS[name]):
            results.append(grader.grade(case))
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    pass_rate = round(passed / len(results), 4) if results else 0.0
    return {
        "schema_version": "harness_eval_report.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "total_cases": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "results": [result.to_dict() for result in results],
    }


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                cases.append(payload)
    return cases


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary_text(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Harness Eval",
            f"Total: {summary['total_cases']}",
            f"Passed: {summary['passed']}",
            f"Failed: {summary['failed']}",
            f"Pass rate: {summary['pass_rate'] * 100:.2f}%",
            f"Report: {summary['report']}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
