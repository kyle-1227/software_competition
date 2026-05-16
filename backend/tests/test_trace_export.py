from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.evals import export_trace
from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.trace_store import TraceStore
from app.services.tracing.reader import (
    find_trace_by_id,
    load_trace_from_jsonl,
    sanitize_trace_for_export,
)


def test_trace_reader_loads_trace_from_jsonl(tmp_path) -> None:
    trace = _trace([_span("node.orchestrator", SpanKind.NODE)])
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(
        "\n".join(
            [
                "{invalid json",
                trace.model_dump_json(),
                json.dumps({"trace_id": "broken"}),
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_trace_from_jsonl(trace_file, trace.trace_id)

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id


def test_find_trace_by_id_prefers_trace_store(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    store.close_trace(trace_id)

    trace = find_trace_by_id(store, trace_id)

    assert trace is not None
    assert trace.trace_id == trace_id


def test_sanitize_trace_for_export_redacts_sensitive_fields() -> None:
    full_script = "print('FULL SCRIPT SHOULD NOT LEAK')\n" * 20
    trace = _trace(
        [
            _span(
                "tool.manual_lookup.attempt",
                SpanKind.TOOL,
                inputs={
                    "api_key": "real-api-key",
                    "access_token": "secret-token",
                    "password": "real-password",
                    "secret": "real-secret",
                    "reasoning": "hidden reasoning",
                    "script": full_script,
                },
                outputs={
                    "answer": "FULL ANSWER SHOULD NOT LEAK " * 20,
                    "evidence": [
                        {
                            "page": 3,
                            "snippet": "LONG EVIDENCE SHOULD NOT LEAK " * 20,
                        }
                    ],
                },
            )
        ],
        question="api_key=real-api-key secret=real-secret",
    )

    sanitized = sanitize_trace_for_export(trace)
    rendered = json.dumps(sanitized, ensure_ascii=False)

    assert "real-api-key" not in rendered
    assert "secret-token" not in rendered
    assert "real-password" not in rendered
    assert "real-secret" not in rendered
    assert "hidden reasoning" not in rendered
    assert full_script not in rendered
    assert "script_hash" in rendered
    assert "evidence_count" in rendered


def test_export_trace_cli_summary(tmp_path) -> None:
    trace = _trace([_span("node.orchestrator", SpanKind.NODE)])
    trace_file = _write_trace_file(tmp_path, trace)
    output = tmp_path / "summary.json"

    code = export_trace.main(
        [
            "--trace-id",
            trace.trace_id,
            "--format",
            "summary",
            "--trace-file",
            str(trace_file),
            "--output",
            str(output),
            "--pretty",
        ]
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    assert code == 0
    assert data["trace_id"] == trace.trace_id
    assert data["span_count"] == 1


def test_export_trace_cli_timeline(tmp_path) -> None:
    full_answer = "FULL ANSWER SHOULD NOT APPEAR " * 20
    full_script = "print('FULL SCRIPT SHOULD NOT APPEAR')\n" * 20
    trace = _trace(
        [
            _span(
                "llm.answer_generation",
                SpanKind.LLM,
                metadata={
                    "llm_model": "local-diagnostic-template",
                    "fallback_used": True,
                    "answer_length": len(full_answer),
                    "answer": full_answer,
                    "script": full_script,
                    "reasoning": "hidden reasoning",
                },
            )
        ]
    )
    trace_file = _write_trace_file(tmp_path, trace)
    output = tmp_path / "timeline.md"

    code = export_trace.main(
        [
            "--trace-id",
            trace.trace_id,
            "--format",
            "timeline",
            "--trace-file",
            str(trace_file),
            "--output",
            str(output),
        ]
    )

    markdown = output.read_text(encoding="utf-8")
    assert code == 0
    assert "# Trace Timeline" in markdown
    assert "llm.answer_generation" in markdown
    assert full_answer not in markdown
    assert full_script not in markdown
    assert "hidden reasoning" not in markdown


def test_export_trace_cli_not_found(tmp_path, capsys) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text("", encoding="utf-8")

    code = export_trace.main(
        [
            "--trace-id",
            "missing",
            "--trace-file",
            str(trace_file),
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "Trace not found: missing" in captured.err


def _write_trace_file(tmp_path, trace: Trace):
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(trace.model_dump_json() + "\n", encoding="utf-8")
    return trace_file


def _trace(
    spans: list[TraceSpan],
    *,
    trace_id: str = "trace-export",
    question: str = "question",
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=1000),
    )
    for index, span in enumerate(spans):
        span.start_time = started + timedelta(milliseconds=index * 10)
        span.end_time = span.start_time + timedelta(milliseconds=10)
        root.children.append(span)
    return Trace(
        trace_id=trace_id,
        session_id="session-1",
        question=question,
        root_span=root,
    )


def _span(
    name: str,
    kind: SpanKind,
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TraceSpan:
    return TraceSpan(
        name=name,
        kind=kind,
        inputs=inputs or {},
        outputs=outputs or {},
        metadata=metadata or {},
    )
