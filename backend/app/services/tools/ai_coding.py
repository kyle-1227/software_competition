from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.services.tool_registry import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_AI_CODING_PROMPT = (
    "你是设备检修系统的诊断脚本生成器。根据任务描述生成可审查的脚本。\n\n"
    "约束:\n"
    "- 不要执行命令，只生成脚本文本\n"
    "- 不要写入系统目录\n"
    "- 不要包含破坏性操作（rm、del、format、shutdown 等）\n"
    "- 包含注释说明需要人工复核\n"
    "- 脚本应该短小、安全、可用于比赛演示\n\n"
    "返回 JSON:\n"
    '{"language": "python"|"sql"|"shell", '
    '"script": "生成的脚本内容", '
    '"explanation": "脚本说明", '
    '"warnings": ["安全提示1", "安全提示2"]}\n'
    "只返回 JSON，不要其他文字。"
)


class AICodingTool(BaseTool):
    name = "ai_coding"
    description = "Generate reviewable Python, SQL, or Shell scripts without executing them."
    parameters_schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "诊断任务描述",
            },
            "question": {
                "type": "string",
                "description": "用户原始问题（task 为空时使用）",
            },
            "language": {
                "type": "string",
                "enum": ["python", "sql", "shell"],
                "description": "目标脚本语言",
            },
        },
        "required": ["task"],
    }

    def __init__(
        self,
        llm_client: Any | None = None,
        prompt_text: str | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._prompt_text = prompt_text or _AI_CODING_PROMPT

    async def run(self, payload: dict[str, Any]) -> ToolResult:
        task = str(payload.get("task") or payload.get("question") or "demo").strip()
        language = str(payload.get("language") or self._infer_language(task)).lower()

        if self._llm_client is not None:
            try:
                return await self._llm_generate(task, language)
            except Exception as exc:
                logger.warning("AI coding LLM generation failed, using fallback: %s", exc)

        return self._build_fallback(task, language)

    async def _llm_generate(self, task: str, language: str) -> ToolResult:
        context = {"task": task, "language": language}
        try:
            response = await self._llm_client.generate_json(
                self._prompt_text, context
            )
        except Exception:
            response = await self._llm_client.generate_text(
                self._prompt_text, context
            )

        text = getattr(response, "text", "")
        if not text:
            return self._build_fallback(task, language)

        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return self._build_fallback(task, language)

        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "language": data.get("language", language),
                "script": data.get("script", ""),
                "explanation": data.get("explanation", f"LLM 生成的 {language} 诊断脚本"),
                "warnings": data.get("warnings", [
                    "LLM 生成的脚本仅用于比赛演示，执行前仍需人工复核。",
                    "SandboxExecutor 会执行安全检查、临时目录隔离和超时限制。",
                ]),
            },
        )

    def _infer_language(self, task: str) -> str:
        lowered = task.lower()
        if "sql" in lowered or "数据库" in task:
            return "sql"
        if "shell" in lowered or "powershell" in lowered or "命令" in task:
            return "shell"
        return "python"

    def _build_fallback(self, task: str, language: str) -> ToolResult:
        if language == "sql":
            script = "SELECT 'diagnostic' AS task;"
        elif language == "shell":
            script = f"Write-Output 'Pending diagnostic automation for: {task}'"
        else:
            script = (
                "# Generated diagnostic helper script.\n"
                "# Review before running in a maintenance environment.\n"
                f"task = {task!r}\n"
                "print(f'Pending diagnostic automation for: {task}')\n"
            )
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "language": language,
                "script": script,
                "explanation": f"基于任务生成 {language} 草案（fallback）。",
                "warnings": [
                    "脚本仅用于比赛演示，执行前仍需人工复核。",
                    "SandboxExecutor 会执行安全检查、临时目录隔离和超时限制。",
                ],
            },
        )
