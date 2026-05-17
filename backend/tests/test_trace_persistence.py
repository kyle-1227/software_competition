from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.persistence import (
    question_persistence_fields,
    sanitize_span_for_persistence,
    sanitize_trace_for_persistence,
)


def test_question_persistence_fields_by_capture_mode() -> None:
    question = "api_key=real-api-key password=real-password " + "q" * 200

    minimal = question_persistence_fields(question, "minimal")
    summary = question_persistence_fields(question, "summary")
    debug = question_persistence_fields(question, "debug")

    assert minimal["question_hash"]
    assert minimal["question_length"] == len(question)
    assert minimal["question_preview"] is None
    assert minimal["question"] is None

    assert summary["question_hash"] == minimal["question_hash"]
    assert len(summary["question_preview"]) <= 120
    assert summary["question"] == summary["question_preview"]
    assert "real-api-key" not in summary["question_preview"]
    assert "real-password" not in summary["question_preview"]

    assert len(debug["question_preview"]) <= 500
    assert "real-api-key" not in debug["question_preview"]
    assert "real-password" not in debug["question_preview"]


def test_sanitize_span_for_persistence_summarizes_scripts_and_redacts_reasoning() -> None:
    full_script = "api_key=real-api-key\nprint('SCRIPT_BODY_SHOULD_NOT_LEAK')\n" * 20
    span = TraceSpan(
        name="tool.ai_coding.attempt",
        kind=SpanKind.TOOL,
        inputs={
            "script": full_script,
            "code": full_script,
            "command": full_script,
            "reasoning": "private reasoning",
            "chain_of_thought": "private chain",
        },
    )
    span.children.append(
        TraceSpan(
            name="node.child",
            kind=SpanKind.NODE,
            outputs={"answer": "ANSWER_BODY_SHOULD_NOT_LEAK " * 20},
        )
    )

    summary = sanitize_span_for_persistence(span, "summary")
    minimal = sanitize_span_for_persistence(span, "minimal")
    rendered = json.dumps(summary, ensure_ascii=False)
    minimal_rendered = json.dumps(minimal, ensure_ascii=False)

    assert full_script not in rendered
    assert "real-api-key" not in rendered
    assert "private reasoning" not in rendered
    assert "private chain" not in rendered
    assert "script_hash" in rendered
    assert "script_length" in rendered
    assert "code_hash" in rendered
    assert "command_hash" in rendered
    assert summary["children"][0]["outputs"]["answer"]["answer_length"] > 0
    assert "ANSWER_BODY_SHOULD_NOT_LEAK " * 20 not in rendered

    assert "script_preview" not in minimal_rendered
    assert "code_preview" not in minimal_rendered
    assert "command_preview" not in minimal_rendered


def test_sanitize_trace_for_persistence_recursively_redacts_large_content() -> None:
    full_answer = "ANSWER_BODY_SHOULD_NOT_LEAK " * 20
    full_snippet = "EVIDENCE_BODY_SHOULD_NOT_LEAK " * 20
    trace = _trace(
        root_inputs={
            "api_key": "real-api-key",
            "access_token": "secret-token",
            "prompt": "PROMPT_BODY_SHOULD_NOT_LEAK " * 20,
        },
        child_outputs={
            "answer": full_answer,
            "evidence": [{"page": 1, "snippet": full_snippet}],
        },
    )

    sanitized = sanitize_trace_for_persistence(trace, "summary")
    rendered = json.dumps(sanitized, ensure_ascii=False)

    assert sanitized["question_hash"]
    assert sanitized["question_preview"]
    assert sanitized["question"] == sanitized["question_preview"]
    assert "real-api-key" not in rendered
    assert "secret-token" not in rendered
    assert "PROMPT_BODY_SHOULD_NOT_LEAK " * 20 not in rendered
    assert full_answer not in rendered
    assert full_snippet not in rendered
    assert sanitized["root_span"]["children"][0]["outputs"]["answer"]["answer_length"] > 0
    assert sanitized["root_span"]["children"][0]["outputs"]["evidence"]["evidence_count"] == 1


def test_script_preview_fields_do_not_leak_sensitive_values() -> None:
    full_script = (
        "curl -H 'api_key=real-api-key' \\\n"
        "-H 'Authorization: Bearer secret-token' \\\n"
        "https://api.example.com\n"
    )
    span = TraceSpan(
        name="tool.ai_coding.attempt",
        kind=SpanKind.TOOL,
        inputs={
            "script": full_script,
            "code": "code with token=my-secret-token here",
            "command": "run --password=super-secret-password",
        },
    )

    summary = sanitize_span_for_persistence(span, "summary")
    inputs = summary["inputs"]

    script_preview = inputs["script"]["script_preview"]
    code_preview = inputs["code"]["code_preview"]
    command_preview = inputs["command"]["command_preview"]

    assert "real-api-key" not in script_preview
    assert "secret-token" not in script_preview
    assert "my-secret-token" not in code_preview
    assert "super-secret-password" not in command_preview
    assert "[REDACTED]" in script_preview


def test_authorization_and_token_patterns_redacted_in_text() -> None:
    span = TraceSpan(
        name="tool.ai_coding.attempt",
        kind=SpanKind.TOOL,
        inputs={
            "script": "auth_header = 'Authorization: Bearer abc-123'\ntoken = 'token=xyz-789'",
        },
    )

    summary = sanitize_span_for_persistence(span, "summary")
    rendered = json.dumps(summary, ensure_ascii=False)

    assert "abc-123" not in rendered
    assert "xyz-789" not in rendered
    assert "[REDACTED]" in rendered


def test_debug_mode_does_not_leak_sensitive_values() -> None:
    full_script = "api_key=real-api-key\npassword=real-password\n" * 30
    span = TraceSpan(
        name="tool.ai_coding.attempt",
        kind=SpanKind.TOOL,
        inputs={
            "script": full_script,
            "code": full_script,
            "command": full_script,
        },
    )

    debug = sanitize_span_for_persistence(span, "debug")
    rendered = json.dumps(debug, ensure_ascii=False)
    inputs = debug["inputs"]

    script_preview = inputs["script"]["script_preview"]

    assert len(script_preview) <= 503
    assert "real-api-key" not in rendered
    assert "real-password" not in rendered
    assert full_script not in rendered


def test_minimal_mode_no_preview_for_scripts() -> None:
    span = TraceSpan(
        name="tool.ai_coding.attempt",
        kind=SpanKind.TOOL,
        inputs={
            "script": "print('hello')",
            "code": "1+1",
            "command": "ls",
        },
    )

    minimal = sanitize_span_for_persistence(span, "minimal")
    inputs = minimal["inputs"]

    assert "script_preview" not in inputs.get("script", {})
    assert "code_preview" not in inputs.get("code", {})
    assert "command_preview" not in inputs.get("command", {})
    assert "script_hash" in inputs["script"]
    assert "script_length" in inputs["script"]


def _trace(
    *,
    root_inputs: dict | None = None,
    child_outputs: dict | None = None,
) -> Trace:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    child = TraceSpan(
        name="llm.answer_generation",
        kind=SpanKind.LLM,
        start_time=started + timedelta(milliseconds=1),
        end_time=started + timedelta(milliseconds=2),
        outputs=child_outputs or {},
    )
    root = TraceSpan(
        name="harness",
        kind=SpanKind.AGENT,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        inputs=root_inputs or {},
        children=[child],
    )
    return Trace(
        trace_id="trace-persistence",
        session_id="session-1",
        question="authorization=Bearer secret-token how to fix?",
        root_span=root,
    )
