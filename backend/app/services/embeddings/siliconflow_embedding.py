from __future__ import annotations

import json
import logging
from typing import Any

from llama_index.core.base.embeddings.base import BaseEmbedding

from app.core.config import settings

logger = logging.getLogger(__name__)

# model_name -> (dimensions, max_chars, description)
_MODEL_SPECS: dict[str, tuple[int, int, str]] = {
    "Qwen/Qwen3-Embedding-8B": (4096, 0, "Qwen3-Embedding-8B"),
    "BAAI/bge-large-zh-v1.5": (1024, 400, "BGE-large-zh-v1.5"),
}


class SiliconFlowEmbedding(BaseEmbedding):
    """Embedding via SiliconFlow with tiered fallback.

    Primary model → fallback model (BGE, free) → ManualHashEmbedding (local).
    When the primary model fails (e.g. insufficient balance), automatically
    degrades to the fallback model for all subsequent calls.
    """

    model_name: str = "Qwen/Qwen3-Embedding-8B"
    dimensions: int = 4096
    embed_batch_size: int = 1

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        fallback_model: str = "BAAI/bge-large-zh-v1.5",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key or settings.siliconflow_api_key
        self._base_url = (base_url or settings.siliconflow_base_url).rstrip("/")
        if model is not None:
            self.model_name = model
        self._fallback_model = fallback_model
        self._active_model: str = self.model_name
        self._degraded: bool = False
        self._warned_fallback_exhausted: bool = False
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
                model=self._active_model, input=texts
            )
            return [d.embedding for d in resp.data]
        except Exception:
            pass
        results: list[list[float]] = []
        for t in texts:
            results.append(self._embed(t))
        return results

    # ---- internals ----

    def _embed(self, text: str) -> list[float]:
        if self._client is None:
            return self._fallback_embed(text)

        model = self._active_model
        _, max_chars, _ = _MODEL_SPECS.get(model, (0, 0, ""))
        truncated = text[:max_chars] if max_chars and len(text) > max_chars else text

        try:
            resp = self._client.embeddings.create(
                model=model, input=truncated
            )
            return resp.data[0].embedding
        except Exception as exc:
            return self._handle_error(exc, text)

    def _handle_error(self, exc: Exception, text: str) -> list[float]:
        msg = str(exc)
        try:
            body = exc.body if hasattr(exc, "body") else None  # type: ignore
        except Exception:
            body = None

        # code 30001 = insufficient balance → degrade to fallback model
        if not self._degraded and self._fallback_model and self._fallback_model != self._active_model:
            if "30001" in msg or (isinstance(body, dict) and body.get("code") == 30001):
                logger.warning(
                    "SiliconFlow %s failed (insufficient balance), "
                    "degrading to %s", self._active_model, self._fallback_model
                )
                self._active_model = self._fallback_model
                self._degraded = True
                dims, _, _ = _MODEL_SPECS.get(self._fallback_model, (0, 0, ""))
                if dims:
                    self.dimensions = dims
                return self._embed(text)

        # After degradation, silence repeated errors (the model is already in fallback)
        if not self._warned_fallback_exhausted:
            logger.warning(
                "SiliconFlow %s also failed (%s), "
                "falling back to ManualHashEmbedding for remaining chunks",
                self._active_model, _error_code(msg, body),
            )
            self._warned_fallback_exhausted = True

        return self._fallback_embed(text)


def _error_code(msg: str, body: Any) -> str:
    if isinstance(body, dict):
        return f"code={body.get('code')}: {body.get('message', '')}"
    return msg[:80]

    def _fallback_embed(self, text: str) -> list[float]:
        from app.services.manual_vector_indexer import ManualHashEmbedding
        return ManualHashEmbedding()._embed(text)
