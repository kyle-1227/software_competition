from __future__ import annotations

import logging
import math
from typing import Any

from llama_index.core.base.embeddings.base import BaseEmbedding

from app.core.config import settings

logger = logging.getLogger(__name__)


class SiliconFlowEmbedding(BaseEmbedding):
    """BAAI/bge-large-zh-v1.5 via SiliconFlow (OpenAI-compatible) API."""

    model_name: str = "BAAI/bge-large-zh-v1.5"
    dimensions: int = 1024
    embed_batch_size: int = 10

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key or settings.siliconflow_api_key
        self._base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        if model is not None:
            self.model_name = model
        self._client = self._build_client()

    def _build_client(self):
        if not self._api_key:
            return None
        try:
            from openai import OpenAI
            return OpenAI(api_key=self._api_key, base_url=self._base_url)
        except Exception as exc:
            logger.warning("SiliconFlow client init failed: %s", exc)
            return None

    # ---- required abstract methods ----

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    # ---- optional overrides ----

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            return [self._fallback_embed(t) for t in texts]
        try:
            resp = self._client.embeddings.create(
                model=self.model_name, input=texts
            )
            return [d.embedding for d in resp.data]
        except Exception as exc:
            logger.warning(
                "SiliconFlow batch embedding failed, falling back: %s", exc
            )
            return [self._fallback_embed(t) for t in texts]

    # ---- internals ----

    def _embed(self, text: str) -> list[float]:
        if self._client is None:
            return self._fallback_embed(text)
        try:
            resp = self._client.embeddings.create(
                model=self.model_name, input=text
            )
            return resp.data[0].embedding
        except Exception as exc:
            logger.warning(
                "SiliconFlow embedding failed, falling back: %s", exc
            )
            return self._fallback_embed(text)

    def _fallback_embed(self, text: str) -> list[float]:
        """Fallback: deterministic hash embedding (same logic as ManualHashEmbedding).

        This ensures the system never crashes, even without API access.
        """
        from app.services.manual_vector_indexer import ManualHashEmbedding
        return ManualHashEmbedding()._embed(text)
