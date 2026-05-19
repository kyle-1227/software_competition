from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.trace import SpanKind
from app.services.agent_loop.policy import AgentLoopPolicy
from app.services.tool_registry import ToolResult
from app.services.tracing.context import trace_span
from app.services.tracing.helpers import summarize_span_payload, summarize_tool_result


class ToolRetryResult(BaseModel):
    success: bool
    degraded: bool
    attempts: int
    result: ToolResult | None = None
    error: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    degradation_reason: str | None = None
    degradation_events: list[dict[str, Any]] = Field(default_factory=list)


class ToolRetryManager:
    def __init__(
        self,
        max_retries: int | None = None,
        backoff_ms: list[int] | None = None,
    ) -> None:
        policy = AgentLoopPolicy.from_settings()
        self.max_retries = max_retries or policy.max_tool_retries
        self.backoff_ms = backoff_ms or policy.retry_backoff_ms

    async def execute(
        self,
        tool_registry: Any,
        tool_name: str,
        payload: dict[str, Any],
    ) -> ToolRetryResult:
        return await execute_tool_with_retry(
            tool_registry,
            tool_name,
            payload,
            max_retries=self.max_retries,
            backoff_ms=self.backoff_ms,
        )


async def execute_tool_with_retry(
    tool_registry: Any,
    tool_name: str,
    payload: dict[str, Any],
    *,
    max_retries: int | None = None,
    backoff_ms: list[int] | None = None,
    trace_store: Any = None,
    trace_id: str | None = None,
    span_prefix: str | None = None,
    caller: str = "unknown",
    risk_level: str = "unknown",
    run_id: str | None = None,
) -> ToolRetryResult:
    policy = AgentLoopPolicy.from_settings()
    attempts_limit = max_retries or policy.max_tool_retries
    delays = backoff_ms or policy.retry_backoff_ms
    tool_calls: list[dict[str, Any]] = []
    last_result: ToolResult | None = None
    last_error: str | None = None

    for attempt in range(1, attempts_limit + 1):
        await _sleep_for_attempt(attempt, delays)
        span_name = f"{span_prefix or f'tool.{tool_name}'}.attempt"
        async with trace_span(
            trace_store,
            trace_id,
            span_name,
            SpanKind.TOOL,
            inputs=summarize_span_payload(payload),
            metadata={
                "tool_name": tool_name,
                "attempt": attempt,
                "max_retries": attempts_limit,
                "degraded": False,
            },
        ) as span:
            started = time.perf_counter()
            try:
                result = await _execute_tool(
                    tool_registry,
                    tool_name,
                    payload,
                    caller=caller,
                    risk_level=risk_level,
                    trace_id=trace_id,
                    run_id=run_id,
                )
            except Exception as exc:
                result = ToolResult(tool_name=tool_name, success=False, error=str(exc))
            duration_ms = int((time.perf_counter() - started) * 1000)
            result.metadata.setdefault("duration_ms", duration_ms)
            status = "success" if result.success else "failed"
            will_retry = not result.success and attempt < attempts_limit
            attempt_metadata = {
                "tool_name": tool_name,
                "attempt": attempt,
                "max_retries": attempts_limit,
                "status": status,
                "success": result.success,
                "degraded": False,
                "duration_ms": result.metadata.get("duration_ms", duration_ms),
                "error_preview": _truncate(str(result.error), 500)
                if result.error
                else None,
                "will_retry": will_retry,
                "final_attempt": not will_retry,
            }
            span.set_metadata(attempt_metadata)
            span.set_outputs(
                {
                    **summarize_tool_result(result),
                    "duration_ms": attempt_metadata["duration_ms"],
                }
            )
        last_result = result
        last_error = result.error
        tool_calls.append(
            _tool_call(
                tool_name,
                payload,
                result,
                attempt,
                duration_ms,
                degraded=False,
            )
        )
        if result.success:
            return ToolRetryResult(
                success=True,
                degraded=False,
                attempts=attempt,
                result=result,
                tool_calls=tool_calls,
            )

    fallback = _degraded_tool_result(tool_name, payload, attempts_limit, last_error)
    event = _degradation_event(
        "tool_degraded",
        tool_name,
        attempts_limit,
        fallback.error or last_error or "tool failed",
        _fallback_name(tool_name),
    )
    tool_calls.append(
        {
            "tool_name": tool_name,
            "input": _sanitize_payload(payload),
            "status": "degraded",
            "attempt": attempts_limit,
            "duration_ms": 0,
            "error": fallback.error or last_error,
            "degraded": True,
            "output": _sanitize_output(fallback.data),
        }
    )
    return ToolRetryResult(
        success=fallback.success,
        degraded=True,
        attempts=attempts_limit,
        result=fallback,
        error=fallback.error or last_error,
        tool_calls=tool_calls,
        degradation_reason=fallback.error or last_error,
        degradation_events=[event],
    )


async def execute_sandbox_with_retry(
    sandbox_executor: Any,
    script: str,
    language: str,
    *,
    max_retries: int | None = None,
    backoff_ms: list[int] | None = None,
    trace_store: Any = None,
    trace_id: str | None = None,
    span_prefix: str | None = None,
) -> ToolRetryResult:
    policy = AgentLoopPolicy.from_settings()
    attempts_limit = max_retries or policy.max_tool_retries
    delays = backoff_ms or policy.retry_backoff_ms
    payload = {"language": language, "script": script}
    tool_calls: list[dict[str, Any]] = []
    last_error: str | None = None
    last_result: ToolResult | None = None

    for attempt in range(1, attempts_limit + 1):
        await _sleep_for_attempt(attempt, delays)
        span_name = f"{span_prefix or 'sandbox.execute'}.attempt"
        async with trace_span(
            trace_store,
            trace_id,
            span_name,
            SpanKind.SANDBOX,
            inputs=summarize_span_payload(payload),
            metadata={
                "tool_name": "sandbox_execute",
                "language": language,
                "attempt": attempt,
                "max_retries": attempts_limit,
                "degraded": False,
            },
        ) as span:
            started = time.perf_counter()
            try:
                sandbox_result = sandbox_executor.execute(script, language)
                data = sandbox_result.model_dump(mode="json")
                success = bool(data.get("allowed") and data.get("return_code") == 0)
                last_error = data.get("error") or data.get("stderr") or None
                result = ToolResult(
                    tool_name="sandbox_execute",
                    success=success,
                    data=data,
                    error=None if success else last_error,
                )
            except Exception as exc:
                last_error = str(exc)
                data = {}
                result = ToolResult(
                    tool_name="sandbox_execute",
                    success=False,
                    error=last_error,
                )
            duration_ms = int((time.perf_counter() - started) * 1000)
            result.metadata.setdefault("duration_ms", duration_ms)
            status = "success" if result.success else "failed"
            will_retry = not result.success and attempt < attempts_limit
            attempt_metadata = {
                "tool_name": "sandbox_execute",
                "language": language,
                "attempt": attempt,
                "max_retries": attempts_limit,
                "status": status,
                "success": result.success,
                "degraded": False,
                "duration_ms": result.metadata.get("duration_ms", duration_ms),
                "error_preview": _truncate(str(result.error), 500)
                if result.error
                else None,
                "will_retry": will_retry,
                "final_attempt": not will_retry,
                "allowed": data.get("allowed"),
                "return_code": data.get("return_code"),
                "timeout": data.get("return_code") == 124,
            }
            span.set_metadata(attempt_metadata)
            span.set_outputs(
                {
                    **summarize_tool_result(result),
                    "duration_ms": attempt_metadata["duration_ms"],
                    "allowed": data.get("allowed"),
                    "return_code": data.get("return_code"),
                }
            )
        last_result = result
        tool_calls.append(
            _tool_call(
                "sandbox_execute",
                payload,
                result,
                attempt,
                duration_ms,
                degraded=False,
            )
        )
        if result.success:
            return ToolRetryResult(
                success=True,
                degraded=False,
                attempts=attempt,
                result=result,
                tool_calls=tool_calls,
            )

    fallback = _sandbox_degraded_result(language)
    event = _degradation_event(
        "sandbox_degraded",
        "sandbox_execute",
        attempts_limit,
        last_error or "sandbox execution failed",
        "blocked sandbox result",
    )
    tool_calls.append(
        {
            "tool_name": "sandbox_execute",
            "input": _sanitize_payload(payload),
            "status": "degraded",
            "attempt": attempts_limit,
            "duration_ms": 0,
            "error": fallback.error,
            "degraded": True,
            "output": fallback.data,
        }
    )
    return ToolRetryResult(
        success=False,
        degraded=True,
        attempts=attempts_limit,
        result=fallback,
        error=fallback.error or last_error,
        tool_calls=tool_calls,
        degradation_reason=fallback.error or last_error,
        degradation_events=[event],
    )


def manual_lookup_degraded_evidence(
    question: str,
    attempts: int,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "source": "manual::degraded",
            "page": None,
            "snippet": "manual_lookup 连续失败，当前没有可用手册证据；不能据此给出确定参数或维修结论。",
            "score": 0.0,
            "metadata": {
                "retriever": "manual_lookup-degraded",
                "retry_attempts": attempts,
                "question": _truncate(question, 160),
                "degradation_reason": reason or "manual_lookup failed",
            },
        }
    ]


async def _sleep_for_attempt(attempt: int, backoff_ms: list[int]) -> None:
    delay_ms = backoff_ms[min(attempt - 1, len(backoff_ms) - 1)] if backoff_ms else 0
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)


def _tool_call(
    tool_name: str,
    payload: dict[str, Any],
    result: ToolResult,
    attempt: int,
    duration_ms: int,
    *,
    degraded: bool,
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "input": _sanitize_payload(payload),
        "status": "success" if result.success else "failed",
        "attempt": attempt,
        "duration_ms": result.metadata.get("duration_ms", duration_ms),
        "error": result.error,
        "degraded": degraded,
        "output": _sanitize_output(result.data) if result.success else None,
    }


def _degraded_tool_result(
    tool_name: str,
    payload: dict[str, Any],
    attempts: int,
    error: str | None,
) -> ToolResult:
    if tool_name == "manual_lookup":
        question = str(payload.get("question", ""))
        if not _placeholder_evidence_allowed():
            return ToolResult(
                tool_name=tool_name,
                success=True,
                data=[],
                error=error,
                metadata={
                    "degraded": True,
                    "retry_attempts": attempts,
                    "placeholder_suppressed": True,
                },
            )
        return ToolResult(
            tool_name=tool_name,
            success=True,
            data=manual_lookup_degraded_evidence(question, attempts, error),
            error=error,
            metadata={"degraded": True, "retry_attempts": attempts},
        )
    if tool_name == "compliance_check":
        return ToolResult(
            tool_name=tool_name,
            success=True,
            data={
                "is_safe": False,
                "is_compliant": False,
                "needs_review": True,
                "issues": ["合规检查失败，建议人工复核"],
            },
            error=error,
            metadata={"degraded": True, "retry_attempts": attempts},
        )
    if tool_name == "ai_coding":
        language = str(payload.get("language", "python"))
        return ToolResult(
            tool_name=tool_name,
            success=True,
            data={
                "language": language,
                "script": "",
                "script_preview": "",
                "script_hash": None,
                "explanation": "脚本生成连续失败，需要人工确认后处理。",
                "warnings": ["ai_coding 连续失败，未生成可执行脚本。"],
                "degraded": True,
                "requires_human_approval": True,
            },
            error=error,
            metadata={"degraded": True, "retry_attempts": attempts},
        )
    return ToolResult(
        tool_name=tool_name,
        success=False,
        error=error or f"{tool_name} failed after {attempts} attempts",
        metadata={"degraded": True, "retry_attempts": attempts},
    )


async def _execute_tool(
    executor: Any,
    tool_name: str,
    payload: dict[str, Any],
    *,
    caller: str,
    risk_level: str,
    trace_id: str | None,
    run_id: str | None,
) -> ToolResult:
    try:
        return await executor.execute(
            tool_name,
            payload,
            caller=caller,
            risk_level=risk_level,
            trace_id=trace_id,
            run_id=run_id,
        )
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword" not in message and "positional" not in message:
            raise
        return await executor.execute(tool_name, payload)


def _sandbox_degraded_result(language: str) -> ToolResult:
    data = {
        "language": language,
        "allowed": False,
        "return_code": None,
        "stdout": "",
        "stderr": "",
        "error": "sandbox execution failed after 5 retries",
        "duration_ms": None,
    }
    return ToolResult(
        tool_name="sandbox_execute",
        success=False,
        data=data,
        error=data["error"],
        metadata={"degraded": True, "retry_attempts": 5},
    )


def _degradation_event(
    event_type: str,
    tool_name: str,
    attempts: int,
    reason: str,
    fallback: str,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "tool_name": tool_name,
        "attempts": attempts,
        "reason": _truncate(reason, 240),
        "fallback": fallback,
    }


def _fallback_name(tool_name: str) -> str:
    return {
        "manual_lookup": "manual_lookup-degraded placeholder evidence",
        "compliance_check": "conservative compliance result",
        "ai_coding": "manual approval placeholder",
    }.get(tool_name, "failed ToolResult")


def _placeholder_evidence_allowed() -> bool:
    app_env = str(getattr(settings, "app_env", "development") or "").lower()
    return app_env in {"development", "dev", "test", "testing", "local"}


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.lower()
        if any(
            secret in lowered
            for secret in (
                "key",
                "token",
                "password",
                "authorization",
                "secret",
                "reasoning",
                "thinking",
                "chain_of_thought",
                "reasoning_content",
            )
        ):
            safe[key] = "[redacted]"
        elif lowered == "answer":
            text = str(value)
            safe[key] = {
                "answer_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "answer_length": len(text),
            }
        elif _is_script_key(lowered):
            text = str(value)
            safe[f"{lowered}_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            safe[f"{lowered}_preview"] = _truncate(text, 120)
        elif isinstance(value, str):
            safe[key] = _truncate(value, 240)
        else:
            safe[key] = value
    return safe


def _sanitize_output(output: Any) -> Any:
    if isinstance(output, dict):
        safe = {}
        for key, value in output.items():
            lowered = str(key).lower()
            if any(
                secret in lowered
                for secret in (
                    "key",
                    "token",
                    "password",
                    "authorization",
                    "secret",
                    "reasoning",
                    "thinking",
                    "chain_of_thought",
                    "reasoning_content",
                )
            ):
                safe[key] = "[redacted]"
            elif lowered == "answer":
                text = str(value)
                safe[key] = {
                    "answer_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "answer_length": len(text),
                }
            elif _is_script_key(lowered):
                text = str(value)
                safe[f"{lowered}_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                safe[f"{lowered}_preview"] = _truncate(text, 120)
            elif isinstance(value, str):
                safe[key] = _truncate(value, 500)
            else:
                safe[key] = _sanitize_output(value)
        return safe
    if isinstance(output, list):
        return [_sanitize_output(item) for item in output[:10]]
    if isinstance(output, str):
        return _truncate(output, 500)
    return output


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def _is_script_key(key: str) -> bool:
    return (
        key in {"script", "code", "command"}
        or "script" in key
        or key in {"source_code", "generated_code", "shell_command"}
    )
