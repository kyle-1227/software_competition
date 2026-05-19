from __future__ import annotations

from typing import Any

from app.services.tool_registry import ToolResult


class ResultVerifier:
    def verify(self, result: ToolResult) -> ToolResult:
        if not isinstance(result, ToolResult):
            return ToolResult(
                tool_name=getattr(result, "tool_name", "unknown"),
                success=False,
                error="tool returned invalid result type",
            )
        if result.tool_name == "manual_lookup" and result.success:
            result = self._verify_manual_lookup(result)
        if result.tool_name == "ai_coding" and result.success:
            result = self._verify_ai_coding(result)
        return result

    def _verify_manual_lookup(self, result: ToolResult) -> ToolResult:
        data = result.data
        if not isinstance(data, list):
            return ToolResult(
                tool_name=result.tool_name,
                success=False,
                error="manual_lookup must return a list of evidence items",
                metadata=result.metadata,
            )
        return result

    def _verify_ai_coding(self, result: ToolResult) -> ToolResult:
        data: Any = result.data
        if not isinstance(data, dict):
            return ToolResult(
                tool_name=result.tool_name,
                success=False,
                error="ai_coding must return structured script data",
                metadata=result.metadata,
            )
        if not data.get("language"):
            data["language"] = "python"
        return result
