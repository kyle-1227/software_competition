from typing import Any

from app.services.tool_registry import BaseTool, ToolResult


class AICodingTool(BaseTool):
    name = "ai_coding"
    description = "Generate reviewable Python, SQL, or Shell scripts without executing them."

    async def run(self, payload: dict[str, Any]) -> ToolResult:
        task = str(payload.get("task") or payload.get("question") or "demo").strip()
        language = str(payload.get("language") or self._infer_language(task)).lower()
        script = self._build_script(task, language)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "language": language,
                "script": script,
                "execution_allowed": True,
                "warnings": [
                    "脚本仅用于比赛演示，执行前仍需人工复核。",
                    "SandboxExecutor 会执行安全检查、临时目录隔离和超时限制。",
                ],
            },
        )

    def _infer_language(self, task: str) -> str:
        lowered = task.lower()
        if "sql" in lowered or "数据库" in task:
            return "sql"
        if "shell" in lowered or "powershell" in lowered or "命令" in task:
            return "shell"
        return "python"

    def _build_script(self, task: str, language: str) -> str:
        if language == "sql":
            return (
                "CREATE TABLE diagnostics (id INTEGER PRIMARY KEY, task TEXT);\n"
                f"INSERT INTO diagnostics (task) VALUES ({task!r});\n"
                "SELECT id, task FROM diagnostics;"
            )
        if language == "shell":
            return (
                "$task = "
                + repr(task)
                + "\nWrite-Output \"Pending diagnostic automation for: $task\"\n"
            )
        return (
            "# Generated diagnostic helper script.\n"
            "# Review before running in a maintenance environment.\n"
            "def main():\n"
            f"    task = {task!r}\n"
            "    print(f'Pending diagnostic automation for: {task}')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
