from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.tracing.eval_dataset import TraceEvalDatasetWriter


def test_dataset_writer_appends_jsonl_and_dedupes_by_case_id(tmp_path) -> None:
    dataset = tmp_path / "nested" / "trace_cases.jsonl"
    writer = TraceEvalDatasetWriter(dataset)
    first = _case(case_id="case-1", trace_id="trace-a")
    duplicate_trace_different_case = _case(case_id="case-2", trace_id="trace-a")

    assert writer.append_case(first) is True
    assert writer.append_case(first) is False
    assert writer.append_case(duplicate_trace_different_case) is True

    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in rows] == ["case-1", "case-2"]


def test_dataset_writer_serializes_before_opening_file(monkeypatch, tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    writer = TraceEvalDatasetWriter(dataset)

    def _boom(*args, **kwargs):
        raise TypeError("serialization failed")

    monkeypatch.setattr("app.services.tracing.eval_dataset.json.dumps", _boom)

    with pytest.raises(TypeError):
        writer.append_case(_case())

    assert not dataset.exists()


def test_dataset_writer_failed_write_does_not_create_exported_line(monkeypatch, tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    writer = TraceEvalDatasetWriter(dataset)

    def _raise_open(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(Path, "open", _raise_open)

    with pytest.raises(OSError):
        writer.append_case(_case())


def test_dataset_writer_redacts_sensitive_and_full_content(tmp_path) -> None:
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    full_answer = "FULL ANSWER SHOULD NOT LEAK " * 20
    full_script = "FULL SCRIPT SHOULD NOT LEAK " * 20
    full_evidence = "FULL EVIDENCE SHOULD NOT LEAK " * 20
    dataset = tmp_path / "cases.jsonl"
    writer = TraceEvalDatasetWriter(dataset)

    case = _case()
    case.update(
        {
            "question_preview": full_question,
            "metadata": {
                "api_key": "real-api-key",
                "token": "real-token",
                "password": "real-password",
                "authorization": "Bearer real-auth",
                "secret": "real-secret",
                "reasoning": "hidden reasoning",
                "chain_of_thought": "hidden chain",
                "answer": full_answer,
                "script": full_script,
                "evidence": [{"snippet": full_evidence}],
            },
        }
    )

    assert writer.append_case(case) is True
    rendered = dataset.read_text(encoding="utf-8")

    for forbidden in (
        full_question,
        full_answer,
        full_script,
        full_evidence,
        "real-api-key",
        "real-token",
        "real-password",
        "real-auth",
        "real-secret",
        "hidden reasoning",
        "hidden chain",
    ):
        assert forbidden not in rendered


def _case(case_id: str = "case-1", trace_id: str = "trace-1") -> dict:
    return {
        "schema_version": "trace_eval_case.v1",
        "case_id": case_id,
        "trace_id": trace_id,
        "source": "trace_export",
        "created_at": "2026-01-01T00:00:00+00:00",
        "question_hash": "hash",
        "question_preview": "safe preview...",
        "session_id": "session",
        "failure_type": "tool_failure",
        "root_cause_span": {"span_id": "span", "name": "tool.x.attempt"},
        "suggested_fix": "inspect tool",
        "reason": "tool failed",
        "trace_status": "error",
        "degraded": False,
        "fallback_used": False,
        "confidence": None,
        "evidence_count": 0,
        "retrieved_pages": [],
        "tool_names": ["x"],
        "guardrail_blocked": False,
        "approval_required": False,
        "expected_behavior": "expected",
        "assertions": [{"type": "failure_type", "expected": "tool_failure"}],
        "metadata": {},
    }
