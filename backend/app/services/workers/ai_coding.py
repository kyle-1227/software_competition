from __future__ import annotations

from typing import Any

from app.services.skills_loader import SkillsLoader
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

        # 2. Generate script via ai_coding tool
        coding_result = await services.tool_registry.execute(
            "ai_coding",
            {
                "task": question,
                "question": question,
                "language": language,
            },
        )

        ai_coding_data = (
            coding_result.data
            if isinstance(coding_result.data, dict)
            else {}
        )
        script = ai_coding_data.get("script", "")
        language = ai_coding_data.get("language", language)

        # 3. Execute in sandbox
        sandbox_result = services.sandbox_executor.execute(script, language)
        sandbox_dict = sandbox_result.model_dump(mode="json")

        ai_coding_output = {
            "language": language,
            "script": script,
            "explanation": ai_coding_data.get("explanation", ""),
            "warnings": ai_coding_data.get("warnings", []),
            "sandbox_result": sandbox_dict,
        }

        tool_calls = [
            {
                "tool_name": "ai_coding",
                "input": {"task": question, "language": language},
                "output": ai_coding_data,
                "status": "success" if coding_result.success else "failed",
                "duration_ms": coding_result.metadata.get("duration_ms"),
            },
            {
                "tool_name": "sandbox_execute",
                "input": {"language": language, "script_preview": script[:120]},
                "output": sandbox_dict,
                "status": "success" if sandbox_result.allowed and sandbox_result.return_code == 0 else "failed",
                "duration_ms": sandbox_result.duration_ms,
            },
        ]

        return {
            "ai_coding": ai_coding_output,
            "sandbox_result": sandbox_dict,
            "tool_calls": tool_calls,
            "worker_outputs": [
                {
                    "worker": self.name,
                    "language": language,
                    "script_preview": script[:200],
                    "sandbox_allowed": sandbox_result.allowed,
                    "sandbox_return_code": sandbox_result.return_code,
                }
            ],
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
