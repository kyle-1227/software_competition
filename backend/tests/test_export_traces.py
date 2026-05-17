from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from app.schemas.trace import SpanKind, Trace, TraceSpan
from scripts.export_traces import (
    _load_jsonl_with_stats,
    export_traces,
    is_forbidden_export_path,
    main,
)


def test_export_traces_dry_run_does_not_write(tmp_path) -> None:
    repository = _FakeRepository([_trace("trace-1")])
    output = tmp_path / "export.jsonl"

    stats = export_traces(repository, output=output, apply=False)

    assert stats.candidates == 1
    assert stats.would_export == 1
    assert stats.exported == 0
    assert not output.exists()
    assert stats.output_path == str(output)


def test_export_traces_apply_writes_sanitized_jsonl(tmp_path) -> None:
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    full_script = "FULL SCRIPT SHOULD NOT LEAK " * 20
    repository = _FakeRepository([
        _trace("trace-1", question=full_question, span_inputs={"script": full_script, "api_key": "real-api-key"})
    ])
    output = tmp_path / "export.jsonl"

    stats = export_traces(repository, output=output, apply=True)
    rendered = output.read_text(encoding="utf-8")

    assert stats.exported == 1
    assert "trace-1" in rendered
    assert full_question not in rendered
    assert full_script not in rendered
    assert "real-api-key" not in rendered


def test_export_traces_filters_status_and_date(tmp_path) -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    repository = _FakeRepository(
        [
            _trace("old-error", status="error", closed_at=now - timedelta(days=10)),
            _trace("new-error", status="error", closed_at=now),
            _trace("old-success", status="success", closed_at=now - timedelta(days=10)),
        ]
    )

    stats = export_traces(
        repository,
        output=tmp_path / "export.jsonl",
        status="error",
        before=now - timedelta(days=1),
        apply=False,
    )

    assert stats.candidates == 1
    assert stats.skipped >= 1


def test_export_traces_no_include_spans_clears_children(tmp_path) -> None:
    repository = _FakeRepository([_trace("trace-1", span_inputs={"code": "print('x')"})])
    output = tmp_path / "export.jsonl"

    export_traces(repository, output=output, apply=True, include_spans=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["root_span"]["children"] == []


def test_export_traces_filters_session_id(tmp_path) -> None:
    repository = _FakeRepository(
        [
            _trace("trace-1", session_id="s1"),
            _trace("trace-2", session_id="s2"),
        ]
    )

    stats = export_traces(repository, output=tmp_path / "export.jsonl", session_id="s1", apply=False)

    assert stats.candidates == 1


def test_export_traces_filters_after_date(tmp_path) -> None:
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    repository = _FakeRepository(
        [
            _trace("old", closed_at=now - timedelta(days=10)),
            _trace("new", closed_at=now),
        ]
    )

    stats = export_traces(
        repository,
        output=tmp_path / "export.jsonl",
        after=now - timedelta(days=1),
        apply=False,
    )

    assert stats.candidates == 1


def test_export_traces_filters_limit(tmp_path) -> None:
    repository = _FakeRepository(
        [_trace(f"trace-{i}", closed_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc)) for i in range(5)]
    )

    stats = export_traces(repository, output=tmp_path / "export.jsonl", limit=2, apply=False)

    assert stats.candidates == 2


def test_export_traces_bad_json_line_not_fatal(tmp_path) -> None:
    dataset = tmp_path / "traces.jsonl"
    good = _trace_dict("good-trace")
    dataset.write_text(
        "not valid json\n" + json.dumps(good) + "\n",
        encoding="utf-8",
    )
    stderr = StringIO()

    traces, skipped, failed = _load_jsonl_with_stats(dataset, stderr)

    assert len(traces) == 1
    assert traces[0].trace_id == "good-trace"
    assert failed == 1


def test_load_jsonl_with_stats_missing_file(tmp_path) -> None:
    traces, skipped, failed = _load_jsonl_with_stats(tmp_path / "missing.jsonl", None)

    assert traces == []
    assert skipped == 0
    assert failed == 0


def test_export_traces_missing_jsonl_path_succeeds(tmp_path) -> None:
    output = tmp_path / "export.jsonl"
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("", encoding="utf-8")

    code = main(["--backend", "jsonl", "--jsonl-path", str(empty_file), "--output", str(output)])

    assert code == 0


def test_export_traces_forbidden_output_path_fails(tmp_path) -> None:
    # is_forbidden_export_path is path-sensitive; covered by direct unit tests.
    # This CLI-level test verifies that a known-forbidden relative path fails.
    output = tmp_path / "export.jsonl"
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("", encoding="utf-8")

    code = main(
        ["--backend", "jsonl", "--jsonl-path", str(empty_file), "--output", "evals/datasets/trace_regression_cases.jsonl"],
    )

    assert code == 1


def test_export_traces_postgres_missing_db_url_fails(tmp_path) -> None:
    output = tmp_path / "export.jsonl"
    stderr = StringIO()

    code = main(
        ["--backend", "postgres", "--database-url", "", "--output", str(output)],
    )

    assert code == 1


def test_export_traces_include_spans_true_by_default(tmp_path) -> None:
    repository = _FakeRepository([_trace("trace-1", span_inputs={"code": "print('x')"})])
    output = tmp_path / "export.jsonl"

    export_traces(repository, output=output, apply=True)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert len(payload["root_span"]["children"]) == 1


def test_export_traces_postgres_fake_repository(tmp_path) -> None:
    output = tmp_path / "export.jsonl"
    fake_repo = _FakeRepository([_trace("pg-trace-1")])

    stats = export_traces(fake_repo, output=output, apply=True)

    assert stats.exported == 1
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["trace_id"] == "pg-trace-1"


def test_is_forbidden_export_path_rejects_reports(tmp_path) -> None:
    assert is_forbidden_export_path(Path("evals/reports/report.json")) is True


def test_is_forbidden_export_path_allows_normal_path(tmp_path) -> None:
    assert is_forbidden_export_path(Path("data/exports/traces.jsonl")) is False


class _FakeRepository:
    def __init__(self, traces):
        self.traces = list(traces)

    def list_traces(self, limit=50, session_id=None, status=None):
        traces = self.traces
        if session_id:
            traces = [trace for trace in traces if trace.session_id == session_id]
        if status:
            traces = [trace for trace in traces if trace.status.value == status]
        if limit is not None:
            traces = traces[:limit]
        return traces


def _trace(
    trace_id: str,
    *,
    status: str = "success",
    session_id: str = "session",
    closed_at: datetime | None = None,
    question: str = "q",
    span_inputs=None,
) -> Trace:
    closed_at = closed_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name="tool.manual_lookup.attempt",
        kind=SpanKind.TOOL,
        inputs=span_inputs or {},
    )
    return Trace(
        trace_id=trace_id,
        session_id=session_id,
        question=question,
        status=status,
        created_at=closed_at - timedelta(seconds=1),
        closed_at=closed_at,
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span]),
    )


def _trace_dict(trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "session_id": "session",
        "question": "q",
        "status": "success",
        "root_span": {"name": "harness", "kind": "agent"},
    }
