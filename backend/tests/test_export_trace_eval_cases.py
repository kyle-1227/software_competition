from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO

from scripts.export_trace_eval_cases import (
    EvalExportOptions,
    export_trace_eval_cases,
    main,
)
from app.schemas.trace import SpanKind, SpanStatus, Trace, TraceSpan


def test_export_cli_dry_run_runs_analytics_without_writing(tmp_path) -> None:
    trace = _failure_trace()
    repository = _FakeRepository([trace])
    dataset = tmp_path / "cases.jsonl"

    stats = export_trace_eval_cases(
        EvalExportOptions(dataset=dataset, apply=False),
        repository=repository,
    )

    assert stats.dry_run is True
    assert stats.traces_seen == 1
    assert stats.eligible == 1
    assert stats.exported == 0
    assert not dataset.exists()


def test_export_cli_apply_writes_dataset_and_deduplicates(tmp_path) -> None:
    trace = _failure_trace()
    repository = _FakeRepository([trace])
    dataset = tmp_path / "cases.jsonl"
    options = EvalExportOptions(dataset=dataset, apply=True)

    first = export_trace_eval_cases(options, repository=repository)
    second = export_trace_eval_cases(options, repository=repository)

    assert first.exported == 1
    assert first.deduplicated == 0
    assert second.exported == 0
    assert second.deduplicated == 1
    rows = dataset.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_export_cli_filters_and_include_success(tmp_path) -> None:
    failure = _failure_trace(trace_id="trace-failure")
    success = _success_trace(trace_id="trace-success")
    repository = _FakeRepository([failure, success])

    without_success = export_trace_eval_cases(
        EvalExportOptions(dataset=tmp_path / "a.jsonl", apply=False),
        repository=repository,
    )
    with_success = export_trace_eval_cases(
        EvalExportOptions(dataset=tmp_path / "b.jsonl", apply=False, include_success=True),
        repository=repository,
    )
    filtered = export_trace_eval_cases(
        EvalExportOptions(
            dataset=tmp_path / "c.jsonl",
            apply=False,
            failure_type="tool_failure",
        ),
        repository=repository,
    )

    assert without_success.traces_seen == 2
    assert without_success.eligible == 1
    assert without_success.skipped == 1
    assert with_success.eligible == 2
    assert filtered.eligible == 1


def test_export_cli_trace_id_filter(tmp_path) -> None:
    trace = _failure_trace(trace_id="trace-one")
    repository = _FakeRepository([trace])

    stats = export_trace_eval_cases(
        EvalExportOptions(dataset=tmp_path / "cases.jsonl", trace_id="missing"),
        repository=repository,
    )

    assert stats.traces_seen == 0
    assert stats.skipped == 1


def test_export_cli_stdout_final_line_is_json(monkeypatch, capsys, tmp_path) -> None:
    repository = _FakeRepository([_failure_trace()])
    monkeypatch.setattr(
        "scripts.export_trace_eval_cases._build_repository",
        lambda options: repository,
    )

    code = main(["--dataset", str(tmp_path / "cases.jsonl")])

    captured = capsys.readouterr()
    stats = json.loads(captured.out.splitlines()[-1])
    assert code == 0
    assert stats["dry_run"] is True
    assert stats["traces_seen"] == 1
    assert stats["eligible"] == 1


def test_export_cli_error_output_is_redacted(tmp_path) -> None:
    trace = _failure_trace()
    repository = _FakeRepository([trace])
    stderr = StringIO()

    stats = export_trace_eval_cases(
        EvalExportOptions(dataset=tmp_path / "cases.jsonl", apply=True),
        repository=repository,
        writer=_FailingWriter(),
        stderr=stderr,
    )

    rendered = stderr.getvalue()
    assert stats.failed == 1
    assert "real-api-key" not in rendered
    assert "real-token" not in rendered
    assert "real-password" not in rendered
    assert "real-secret" not in rendered


def test_exported_eval_case_json_does_not_leak_sensitive_or_full_content(tmp_path) -> None:
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    full_answer = "FULL ANSWER SHOULD NOT LEAK " * 20
    full_script = "FULL SCRIPT SHOULD NOT LEAK " * 20
    full_evidence = "FULL EVIDENCE SHOULD NOT LEAK " * 20
    trace = _failure_trace(
        question=full_question,
        inputs={
            "api_key": "real-api-key",
            "token": "real-token",
            "password": "real-password",
            "authorization": "Bearer real-auth",
            "secret": "real-secret",
            "reasoning": "hidden reasoning",
            "chain_of_thought": "hidden chain",
            "script": full_script,
        },
        outputs={
            "answer": full_answer,
            "evidence": [{"snippet": full_evidence}],
        },
    )
    dataset = tmp_path / "cases.jsonl"

    export_trace_eval_cases(
        EvalExportOptions(dataset=dataset, apply=True),
        repository=_FakeRepository([trace]),
    )
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


class _FakeRepository:
    def __init__(self, traces: list[Trace]):
        self.traces = {trace.trace_id: trace for trace in traces}

    def list_traces(self, limit=50, session_id=None, status=None):
        traces = list(self.traces.values())
        if session_id:
            traces = [trace for trace in traces if trace.session_id == session_id]
        if status:
            traces = [trace for trace in traces if str(trace.status.value) == status]
        return traces[:limit]

    def get_trace(self, trace_id):
        return self.traces.get(trace_id)


class _FailingWriter:
    def append_case(self, case):
        raise RuntimeError(
            "api_key=real-api-key token=real-token password=real-password secret=real-secret"
        )


def _failure_trace(
    *,
    trace_id: str = "trace-failure",
    question: str = "why did it fail?",
    inputs=None,
    outputs=None,
) -> Trace:
    return _trace(
        trace_id=trace_id,
        question=question,
        spans=[
            TraceSpan(
                name="tool.manual_lookup.attempt",
                kind=SpanKind.TOOL,
                status=SpanStatus.ERROR,
                inputs=inputs or {},
                outputs=outputs or {},
            )
        ],
        status="error",
    )


def _success_trace(trace_id: str = "trace-success") -> Trace:
    return _trace(
        trace_id=trace_id,
        question="successful grounded case",
        spans=[
            TraceSpan(
                name="retriever.vector_search",
                kind=SpanKind.RETRIEVER,
                metadata={"evidence_count": 2, "retrieved_pages": [1]},
            )
        ],
        status="success",
    )


def _trace(trace_id: str, question: str, spans: list[TraceSpan], status: str) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, span in enumerate(spans):
        span.start_time = started + timedelta(milliseconds=index * 10)
        span.end_time = span.start_time + timedelta(milliseconds=10)
        span.duration_ms = 10
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        duration_ms=100,
        children=spans,
    )
    return Trace(
        trace_id=trace_id,
        session_id="session-export",
        question=question,
        root_span=root,
        status=status,
        total_duration_ms=100,
    )
