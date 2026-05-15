from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.retriever import Retriever
from app.services.tool_registry import BaseTool, ToolResult


class ManualLookupTool(BaseTool):
    name = "manual_lookup"
    description = "Search maintenance manuals and return structured evidence."
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "用户故障描述或查询问题",
            },
            "device_name": {
                "type": "string",
                "description": "设备名称（可选）",
            },
            "device_model": {
                "type": "string",
                "description": "设备型号（可选）",
            },
        },
        "required": ["question"],
    }

    def __init__(
        self,
        retriever: Retriever | None = None,
        llm_client: Any | None = None,
    ) -> None:
        if retriever is not None:
            self.retriever = retriever
        else:
            self.retriever = Retriever(
                reranker=_build_reranker(),
                query_rewriter=_build_query_rewriter(llm_client),
            )

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


def _build_reranker():
    if not (settings.reranker_enabled and settings.siliconflow_api_key):
        return None
    try:
        from app.services.reranker import SiliconFlowReranker
        return SiliconFlowReranker()
    except Exception:
        return None


def _build_query_rewriter(llm_client: Any | None):
    if not (settings.hyde_enabled and llm_client):
        return None
    try:
        from app.services.reranker import QueryRewriter
        return QueryRewriter(llm_client=llm_client)
    except Exception:
        return None
