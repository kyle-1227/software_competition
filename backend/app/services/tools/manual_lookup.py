from typing import Any

from app.services.retriever import Retriever
from app.services.tool_registry import BaseTool, ToolResult


class ManualLookupTool(BaseTool):
    name = "manual_lookup"
    description = "Search maintenance manuals and return structured evidence."

    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever = retriever or Retriever()

    async def run(self, payload: dict[str, Any]) -> ToolResult:
        question = str(payload.get("question", "")).strip()
        device_model = payload.get("device_model")
        evidence = await self.retriever.search(question, device_model)
        return ToolResult(
            tool_name=self.name,
            success=True,
            data=[item.model_dump(mode="json") for item in evidence],
            metadata={"evidence_count": len(evidence)},
        )
