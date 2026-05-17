from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.config import settings
from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.reader import sanitize_trace_for_export
from app.services.tracing.serializers import sanitize_trace_dict


def test_sanitizer_redacts_sensitive_and_reasoning_fields() -> None:
    sanitized = sanitize_trace_dict(
        {
            "api_key": "real-api-key",
            "authorization": "Bearer secret-token",
            "password": "real-password",
            "secret": "real-secret",
            "reasoning": "private reasoning",
            "reasoning_content": "private reasoning content",
            "thinking": "private thinking",
            "chain_of_thought": "private chain",
        }
    )

    rendered = json.dumps(sanitized, ensure_ascii=False)
    assert "real-api-key" not in rendered
    assert "secret-token" not in rendered
    assert "real-password" not in rendered
    assert "real-secret" not in rendered
    assert "private reasoning" not in rendered
    assert "private thinking" not in rendered


def test_minimal_capture_mode_does_not_keep_text_preview(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")
    sanitized = sanitize_trace_dict(
        {
            "prompt": "PROMPT SHOULD NOT BE PREVIEWED",
            "answer": "ANSWER SHOULD NOT BE PREVIEWED",
            "script": "print('SCRIPT SHOULD NOT BE PREVIEWED')",
            "evidence": [{"page": 1, "snippet": "EVIDENCE SHOULD NOT BE PREVIEWED"}],
        }
    )
    rendered = json.dumps(sanitized, ensure_ascii=False)

    assert "PROMPT SHOULD NOT BE PREVIEWED" not in rendered
    assert "ANSWER SHOULD NOT BE PREVIEWED" not in rendered
    assert "SCRIPT SHOULD NOT BE PREVIEWED" not in rendered
    assert "EVIDENCE SHOULD NOT BE PREVIEWED" not in rendered
    assert "prompt_preview" not in rendered
    assert "answer_preview" not in rendered
    assert "script_preview" not in rendered
    assert "top_snippet_preview" not in rendered
    assert "prompt_hash" in rendered
    assert "answer_hash" in rendered
    assert "script_hash" in rendered


def test_raw_export_respects_minimal_capture_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trace_capture_mode", "minimal")
    trace = Trace(
        trace_id="minimal-export-trace",
        session_id="session-1",
        question="QUESTION SHOULD NOT BE PREVIEWED",
        root_span=TraceSpan(
            name="harness",
            kind=SpanKind.AGENT,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            children=[
                TraceSpan(
                    name="node.test",
                    kind=SpanKind.NODE,
                    outputs={"answer": "ANSWER SHOULD NOT BE PREVIEWED"},
                )
            ],
        ),
    )

    rendered = json.dumps(sanitize_trace_for_export(trace), ensure_ascii=False)

    assert "QUESTION SHOULD NOT BE PREVIEWED" not in rendered
    assert "ANSWER SHOULD NOT BE PREVIEWED" not in rendered
    assert "question_preview" not in rendered
    assert "answer_preview" not in rendered
