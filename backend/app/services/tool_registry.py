import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict[str, Any] | list[dict[str, Any]] | str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    name: str
    description: str
    parameters_schema: dict[str, Any] = {}

    @abstractmethod
    async def run(self, payload: dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def to_openai_function(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema
                or {"type": "object", "properties": {}},
            },
        }


class ToolRegistry:
    """Small tool runtime used by AgentHarness.

    LangChain tool adapters can wrap these classes later without changing
    the Harness contract.
    """

    def __init__(
        self,
        register_defaults: bool = True,
        llm_client: Any = None,
    ) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._llm_client = llm_client
        if register_defaults:
            self._register_default_tools()

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Tool not registered: {name}") from exc

    async def execute(self, name: str, payload: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        try:
            result = await self.get(name).run(payload)
        except Exception as exc:
            result = ToolResult(tool_name=name, success=False, error=str(exc))
        duration_ms = int((time.perf_counter() - started) * 1000)
        result.metadata.setdefault("duration_ms", duration_ms)
        return result

    def _register_default_tools(self) -> None:
        from app.services.tools.ai_coding import AICodingTool
        from app.services.tools.compliance_check import ComplianceCheckTool
        from app.services.tools.manual_lookup import ManualLookupTool

        self.register(ManualLookupTool())
        self.register(AICodingTool(llm_client=self._llm_client))
        self.register(ComplianceCheckTool())
