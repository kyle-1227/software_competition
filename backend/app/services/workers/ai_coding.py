from __future__ import annotations

from typing import Any

from app.schemas.trace import SpanKind
from app.services.agent_loop.retry import (
    execute_sandbox_with_retry,
    execute_tool_with_retry,
)
from app.services.skills_loader import SkillsLoader
from app.services.tracing.context import trace_span
from app.services.workers.base import BaseWorker


class AICodingWorker(BaseWorker):
    """AI 编程 Worker: LLM 生成真实诊断脚本 → 沙箱执行。

    加载 `skills/ai_coding/` 下的 skill.md 和 AGENT.md。
    """

    name = "ai_coding"
    description = "生成诊断脚本（Python/SQL/Shell）并在沙箱中安全执行"
    tools = ["ai_coding", "sandbox_execute"]

    def __init__(self) -> None:
        self._skill_def = SkillsLoader.load("ai_coding")

    async def execute(
        self, state: dict[str, Any], services: Any
    ) -> dict[str, Any]:
        question = state.get("question", "")

        # 1. Determine language
        language = self._infer_language(question, state)

        # 2. Generate script via ai_coding tool, with bounded retry.
        coding_retry = await execute_tool_with_retry(
            getattr(services, "tool_broker", services.tool_registry),
            "ai_coding",
            {
                "task": question,
                "question": question,
                "language": language,
            },
            trace_store=getattr(services, "trace_store", None),
            trace_id=state.get("trace_id"),
            caller=state.get("intent", self.name),
            risk_level=state.get("risk_level", "unknown"),
            run_id=state.get("trace_id"),
        )
        coding_result = coding_retry.result

        ai_coding_data = (
            coding_result.data
            if coding_result is not None and isinstance(coding_result.data, dict)
            else {}
        )
        if coding_retry.degraded or not coding_retry.success:
            async with trace_span(
                getattr(services, "trace_store", None),
                state.get("trace_id"),
                "sandbox.execute.skipped",
                SpanKind.SANDBOX,
                inputs={"language": ai_coding_data.get("language", language)},
                metadata={
                    "reason": "ai_coding_degraded",
                    "requires_human_approval": True,
                    "degraded": True,
                },
            ) as span:
                span.set_outputs(
                    {
                        "degraded": True,
                        "requires_human_approval": True,
                    }
                )
            ai_coding_output = {
                "language": ai_coding_data.get("language", language),
                "script": "",
                "script_preview": "",
                "script_hash": None,
                "explanation": ai_coding_data.get(
                    "explanation", "脚本生成失败，需要人工确认。"
                ),
                "warnings": ai_coding_data.get(
                    "warnings", ["脚本生成连续失败，未进入 sandbox 执行。"]
                ),
                "degraded": True,
                "requires_human_approval": True,
            }
            return {
                "ai_coding": ai_coding_output,
                "tool_calls": coding_retry.tool_calls,
                "worker_outputs": [
                    {
                        "worker": self.name,
                        "language": ai_coding_output["language"],
                        "script_preview": "",
                        "degraded": True,
                        "retry_attempts": coding_retry.attempts,
                        "requires_human_approval": True,
                    }
                ],
                "degraded": True,
                "retry_attempts": coding_retry.attempts,
                "degradation_events": coding_retry.degradation_events,
                "requires_human_approval": True,
                "approval_reason": "ai_coding 连续失败，需要人工确认",
                "warnings": state.get("warnings", [])
                + ["ai_coding 连续失败，未进入 sandbox 执行。"],
            }

        script = ai_coding_data.get("script", "")
        language = ai_coding_data.get("language", language)

        # 3. Execute in sandbox with bounded retry.
        sandbox_retry = await execute_sandbox_with_retry(
            services.sandbox_executor,
            script,
            language,
            trace_store=getattr(services, "trace_store", None),
            trace_id=state.get("trace_id"),
        )
        sandbox_dict = (
            sandbox_retry.result.data
            if sandbox_retry.result is not None and isinstance(sandbox_retry.result.data, dict)
            else {}
        )
        sandbox_allowed = bool(
            sandbox_dict.get("allowed") and sandbox_dict.get("return_code") == 0
        )
        if sandbox_retry.degraded or not sandbox_allowed:
            script_for_response = ""
        else:
            script_for_response = script

        ai_coding_output = {
            "language": language,
            "script": script_for_response,
            "explanation": ai_coding_data.get("explanation", ""),
            "warnings": ai_coding_data.get("warnings", []),
            "sandbox_result": sandbox_dict,
            "degraded": sandbox_retry.degraded,
        }

        tool_calls = coding_retry.tool_calls + sandbox_retry.tool_calls
        degradation_events = (
            coding_retry.degradation_events + sandbox_retry.degradation_events
        )

        return {
            "ai_coding": ai_coding_output,
            "sandbox_result": sandbox_dict,
            "tool_calls": tool_calls,
            "worker_outputs": [
                {
                    "worker": self.name,
                    "language": language,
                    "script_preview": script[:200],
                    "sandbox_allowed": sandbox_dict.get("allowed"),
                    "sandbox_return_code": sandbox_dict.get("return_code"),
                    "degraded": sandbox_retry.degraded,
                    "retry_attempts": max(coding_retry.attempts, sandbox_retry.attempts),
                }
            ],
            "degraded": sandbox_retry.degraded,
            "retry_attempts": max(coding_retry.attempts, sandbox_retry.attempts),
            "degradation_events": degradation_events,
            "requires_human_approval": sandbox_retry.degraded,
            "approval_reason": (
                "sandbox execution failed after retries"
                if sandbox_retry.degraded
                else state.get("approval_reason")
            ),
            "warnings": state.get("warnings", [])
            + (
                ["sandbox execution failed after 5 retries"]
                if sandbox_retry.degraded
                else []
            ),
        }

    def _infer_language(
        self, question: str, state: dict[str, Any]
    ) -> str:
        lowered = question.lower()
        if "sql" in lowered or "数据库" in question:
            return "sql"
        if "shell" in lowered or "powershell" in lowered or "命令" in question:
            return "shell"
        return "python"
