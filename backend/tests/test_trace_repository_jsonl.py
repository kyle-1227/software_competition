from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.schemas.trace import SpanKind, Trace, TraceSpan, TraceStatus
from app.services.tracing.repository import JsonlTraceRepository


def test_jsonl_repository_save_get_list_and_health(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    repository.initialize()
    trace = _trace()
    span = trace.root_span.children[0]

    repository.save_trace(trace)
    repository.save_span(trace.trace_id, span)
    repository.close_trace(trace)

    loaded = repository.get_trace(trace.trace_id)
    traces = repository.list_traces(limit=5, session_id=trace.session_id)
    summaries = repository.list_trace_summaries(limit=5, session_id=trace.session_id)
    spans = repository.list_spans(trace.trace_id)
    health = repository.health_status()

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id
    assert traces[0].status == TraceStatus.SUCCESS
    assert summaries[0]["span_count"] == 1
    assert summaries[0]["error_count"] == 0
    assert summaries[0]["slowest_span_name"] == "node.orchestrator"
    assert spans[0].name == "node.orchestrator"
    assert health.backend == "jsonl"
    assert health.healthy is True
    assert health.degraded is False
    assert health.ever_degraded is False
    assert health.last_success_at is not None


def test_jsonl_repository_persists_sanitized_trace(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    repository.initialize()
    full_script = "api_key=real-api-key\nprint('SCRIPT_BODY_SHOULD_NOT_LEAK')\n" * 20
    full_answer = "ANSWER_BODY_SHOULD_NOT_LEAK " * 20
    trace = _trace(
        question="token=secret-token password=real-password why fail?",
        span_inputs={
            "api_key": "real-api-key",
            "authorization": "Bearer secret-token",
            "password": "real-password",
            "secret": "real-secret",
            "reasoning": "private reasoning",
            "chain_of_thought": "private chain",
            "script": full_script,
        },
        span_outputs={"answer": full_answer},
    )

    repository.close_trace(trace)

    content = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    loaded = repository.get_trace(trace.trace_id)

    assert loaded is not None
    assert loaded.trace_id == trace.trace_id
    assert "real-api-key" not in content
    assert "secret-token" not in content
    assert "real-password" not in content
    assert "real-secret" not in content
    assert "private reasoning" not in content
    assert "private chain" not in content
    assert full_script not in content
    assert full_answer not in content
    assert "script_hash" in content
    assert "answer_length" in content


def test_jsonl_repository_health_recovers_after_storage_error(tmp_path) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("blocked", encoding="utf-8")
    repository = JsonlTraceRepository(blocking_file)

    failed = repository.health_status()

    assert failed.healthy is False
    assert failed.degraded is True
    assert failed.ever_degraded is True
    assert failed.last_error_at is not None

    repository.storage_path = tmp_path / "recovered"
    recovered = repository.health_status()

    assert recovered.healthy is True
    assert recovered.degraded is False
    assert recovered.ever_degraded is True
    assert recovered.last_success_at is not None


def test_jsonl_repository_minimal_mode_drops_text_previews(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")
    repository = JsonlTraceRepository(tmp_path)
    trace = _trace(
        question="QUESTION SHOULD NOT BE PREVIEWED",
        span_inputs={"script": "print('SCRIPT SHOULD NOT BE PREVIEWED')"},
        span_outputs={"answer": "ANSWER SHOULD NOT BE PREVIEWED"},
    )

    repository.close_trace(trace)

    content = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    payload = json.loads(content)

    assert payload["question_preview"] is None
    assert payload["question"] is None
    assert "QUESTION SHOULD NOT BE PREVIEWED" not in content
    assert "ANSWER SHOULD NOT BE PREVIEWED" not in content
    assert "SCRIPT SHOULD NOT BE PREVIEWED" not in content
    assert "answer_preview" not in content
    assert "script_preview" not in content


def test_jsonl_repository_reads_legacy_trace(tmp_path) -> None:
    legacy = {
        "trace_id": "legacy-jsonl-trace",
        "session_id": "legacy-session",
        "question": "legacy question",
        "status": "ok",
        "root_span": {"name": "harness", "kind": "agent"},
    }
    (tmp_path / "traces.jsonl").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    repository = JsonlTraceRepository(tmp_path)

    loaded = repository.get_trace("legacy-jsonl-trace")

    assert loaded is not None
    assert loaded.question == "legacy question"
    assert loaded.status == TraceStatus.SUCCESS


def test_jsonl_content_redacts_script_preview_sensitive_values(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    repository.initialize()

    script_with_sensitive = (
        "curl -H 'Authorization: Bearer abc-123' \\\n"
        "-H 'api_key=secret-key-here' \\\n"
        "https://api.example.com\n"
    )
    trace = _trace(
        question="check the api",
        span_inputs={
            "script": script_with_sensitive,
            "code": "token=code-token-value",
            "command": "cmd --password=cmd-pass-value --secret=cmd-secret-value",
        },
        span_outputs={"answer": "done"},
    )

    repository.close_trace(trace)

    content = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    payload = json.loads(content)

    rendered = json.dumps(payload, ensure_ascii=False)

    assert "abc-123" not in rendered
    assert "secret-key-here" not in rendered
    assert "code-token-value" not in rendered
    assert "cmd-pass-value" not in rendered
    assert "cmd-secret-value" not in rendered
    assert "[REDACTED]" in rendered


def test_jsonl_content_redacts_auth_token_password_secret_in_question(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    repository.initialize()

    trace = _trace(
        question="authorization=Bearer live-token password=live-pass secret=live-secret help?",
        span_inputs={"prompt": "token=my-token-value"},
    )

    repository.close_trace(trace)

    content = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    rendered = content

    assert "live-token" not in rendered
    assert "live-pass" not in rendered
    assert "live-secret" not in rendered
    assert "my-token-value" not in rendered
    assert "[REDACTED]" in rendered


def _trace(
    *,
    question: str = "q",
    span_inputs: dict | None = None,
    span_outputs: dict | None = None,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span = TraceSpan(
        name="node.orchestrator",
        kind=SpanKind.NODE,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
        inputs=span_inputs or {},
        outputs=span_outputs or {},
    )
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        duration_ms=100,
        children=[span],
    )
    return Trace(
        trace_id="jsonl-repository-trace",
        session_id="jsonl-session",
        question=question,
        root_span=root,
        status="success",
        total_duration_ms=100,
    )
