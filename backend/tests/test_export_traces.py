from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, Trace, TraceSpan
from scripts.export_traces import export_traces


def test_export_traces_dry_run_does_not_write(tmp_path) -> None:
    repository = _FakeRepository([_trace("trace-1")])
    output = tmp_path / "export.jsonl"

    stats = export_traces(repository, output=output, apply=False)

    assert stats.candidates == 1
    assert stats.would_export == 1
    assert stats.exported == 0
    assert not output.exists()


def test_export_traces_apply_writes_sanitized_jsonl(tmp_path) -> None:
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    full_script = "FULL SCRIPT SHOULD NOT LEAK " * 20
    repository = _FakeRepository([
        _trace("trace-1", question=full_question, span_inputs={"script": full_script, "api_key": "real-api-key"})
    ])
    output = tmp_path / "export.jsonl"

    stats = export_traces(repository, output=output, apply=True, include_spans=True)
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
    assert stats.skipped == 1


def test_export_traces_excludes_spans_by_default(tmp_path) -> None:
    repository = _FakeRepository([_trace("trace-1", span_inputs={"code": "print('x')"})])
    output = tmp_path / "export.jsonl"

    export_traces(repository, output=output, apply=True, include_spans=False)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["root_span"]["children"] == []


class _FakeRepository:
    def __init__(self, traces):
        self.traces = list(traces)

    def list_traces(self, limit=50, session_id=None, status=None):
        traces = self.traces
        if session_id:
            traces = [trace for trace in traces if trace.session_id == session_id]
        if status:
            traces = [trace for trace in traces if trace.status.value == status]
        return traces[:limit]


def _trace(
    trace_id: str,
    *,
    status: str = "success",
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
        session_id="session",
        question=question,
        status=status,
        created_at=closed_at - timedelta(seconds=1),
        closed_at=closed_at,
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT, children=[span]),
    )
