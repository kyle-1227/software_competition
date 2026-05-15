from __future__ import annotations

from pathlib import Path

from app.evals.metrics import (
    build_comparison_report,
    compute_metrics,
    load_cases,
)


DATASET = Path(__file__).resolve().parents[1] / "app" / "evals" / "datasets" / "manual_qa_20.jsonl"


def test_eval_dataset_has_20_cases() -> None:
    assert len(load_cases(DATASET)) >= 20


def test_eval_dataset_required_fields() -> None:
    required = {
        "id",
        "question",
        "device_name",
        "expected_pages",
        "expected_terms",
        "expected_answer_type",
        "risk_level",
        "notes",
    }
    for case in load_cases(DATASET):
        assert required.issubset(case)


def test_metrics_page_hit_at_1_and_3() -> None:
    metrics = compute_metrics(
        [
            {
                "expected_pages": [3],
                "retrieved_pages": [3, 4, 5],
                "expected_terms": [],
            },
            {
                "expected_pages": [8],
                "retrieved_pages": [1, 2, 8],
                "expected_terms": [],
            },
        ]
    )

    assert metrics["retrieval_page_hit_at_1"] == 0.5
    assert metrics["retrieval_page_hit_at_3"] == 1.0


def test_metrics_placeholder_rate() -> None:
    metrics = compute_metrics(
        [
            {"placeholder_used": True, "expected_terms": []},
            {"placeholder_used": False, "expected_terms": []},
        ]
    )

    assert metrics["placeholder_rate"] == 0.5


def test_metrics_expected_term_recall() -> None:
    metrics = compute_metrics(
        [
            {
                "expected_terms": ["火花塞", "间隙"],
                "matched_terms": ["火花塞"],
            }
        ]
    )

    assert metrics["expected_term_recall"] == 0.5


def test_comparison_report_generation() -> None:
    old = {
        "cases": [
            {
                "id": "case-1",
                "question": "q",
                "expected_pages": [3],
                "retrieved_pages": [],
                "expected_terms": ["火花塞"],
                "matched_terms": [],
                "placeholder_used": True,
                "latency_ms": 10,
            }
        ]
    }
    new = {
        "cases": [
            {
                "id": "case-1",
                "question": "q",
                "expected_pages": [3],
                "retrieved_pages": [3],
                "expected_terms": ["火花塞"],
                "matched_terms": ["火花塞"],
                "placeholder_used": False,
                "latency_ms": 8,
            }
        ]
    }

    report = build_comparison_report(old, new)

    assert "Overall Metrics" in report
    assert "Improved Cases" in report
    assert "case-1" in report
