from typing import Any

from app.services.tool_registry import BaseTool, ToolResult


class AICodingTool(BaseTool):
    name = "ai_coding"
    description = "Generate reviewable Python helper scripts without executing them."

    async def run(self, payload: dict[str, Any]) -> ToolResult:
        task = str(payload.get("task") or payload.get("question") or "demo").strip()
        script = (
            "# Generated diagnostic helper script.\n"
            "# Review before running in a maintenance environment.\n"
            "def main():\n"
            f"    task = {task!r}\n"
            "    print(f'Pending diagnostic automation for: {task}')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        return ToolResult(
            tool_name=self.name,
            success=True,
            data={
                "language": "python",
                "script": script,
                "warnings": [
                    "Script text is generated only; the Harness does not execute it.",
                    "Review device safety requirements before any field use.",
                ],
            },
        )
