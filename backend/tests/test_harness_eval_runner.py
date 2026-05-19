from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_eval import load_cases, run_eval, write_report


def test_harness_eval_runner_passes_all_seed_cases(tmp_path: Path) -> None:
    report = run_eval(suite="all")

    assert report["schema_version"] == "harness_eval_report.v1"
    assert report["total_cases"] == 10
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0

    report_path = tmp_path / "report.json"
    write_report(report, report_path)
    assert report_path.exists()


def test_retrieval_dataset_has_expected_seed_cases() -> None:
    cases = load_cases(Path("evals/datasets/retrieval_cases.jsonl"))

    assert cases[0]["id"] == "ret_001"
    assert cases[0]["expected_keywords"] == ["火花塞", "间隙"]
    assert cases[0]["expected_page"] is None
    assert cases[1]["id"] == "ret_002"
