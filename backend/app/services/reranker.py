from __future__ import annotations

import json
import logging
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

RERANK_API_PATH = "/rerank"

HYDE_PROMPT = (
    "你是一本摩托车发动机维修手册。请用100-200字写一段内容，"
    "回答以下问题。使用手册式的语言风格，包含具体数值。\n"
    "问题：{question}"
)


class SiliconFlowReranker:
    """Reranker via SiliconFlow with tiered fallback.

    Primary: Qwen/Qwen3-VL-Reranker-8B
    Fallback: BAAI/bge-reranker-v2-m3
    Ultimate fallback: identity (no re-ranking)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        top_n: int | None = None,
    ) -> None:
        self._api_key = api_key or settings.siliconflow_api_key
        self._base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        self.model = model or settings.reranker_model
        self._fallback_model = fallback_model or settings.reranker_fallback_model
        self.top_n = top_n if top_n is not None else settings.reranker_top_n

    def rerank(
        self, query: str, documents: list[str]
    ) -> list[tuple[int, float]]:
        """Re-rank documents against query. Returns [(original_index, score), ...]."""
        if not self._api_key or not documents:
            return self._identity(list(range(len(documents))))

        result = self._call(query, documents, self.model)
        if result is not None:
            return result

        # Fallback to secondary model
        if self._fallback_model and self._fallback_model != self.model:
            logger.info(
                "Reranker %s failed, trying fallback %s",
                self.model, self._fallback_model,
            )
            result = self._call(query, documents, self._fallback_model)
            if result is not None:
                return result

        # Ultimate fallback: no re-ranking
        return self._identity(list(range(len(documents))))

    def _call(
        self, query: str, documents: list[str], model: str
    ) -> list[tuple[int, float]] | None:
        try:
            resp = requests.post(
                f"{self._base_url}{RERANK_API_PATH}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "query": query,
                    "documents": documents,
                    "top_n": min(self.top_n, len(documents)),
                },
                timeout=30,
            )
            data = resp.json()
            if resp.status_code != 200:
                logger.warning(
                    "Reranker %s returned %d: %s", model, resp.status_code, data
                )
                return None
            results = data.get("results", [])
            return [(r["index"], r["relevance_score"]) for r in results]
        except Exception as exc:
            logger.warning("Reranker %s call failed: %s", model, exc)
            return None

    @staticmethod
    def _identity(indices: list[int]) -> list[tuple[int, float]]:
        return [(i, 0.0) for i in indices]


class QueryRewriter:
    """HyDE-style query rewriting: generate a hypothetical manual excerpt,
    then append it to the original query for better vector retrieval.
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm = llm_client

    async def rewrite(self, question: str) -> str:
        if self._llm is None:
            return question

        try:
            response = await self._llm.generate_text(
                HYDE_PROMPT.format(question=question)
            )
            hyde_text = getattr(response, "text", "")
            if hyde_text and len(hyde_text.strip()) > 10:
                return f"{question}\n{hyde_text.strip()}"
        except Exception as exc:
            logger.warning("HyDE query rewrite failed: %s", exc)

        return question
