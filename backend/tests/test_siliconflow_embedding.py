from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.embeddings.siliconflow_embedding import SiliconFlowEmbedding
from app.services.manual_vector_indexer import ManualHashEmbedding, _default_embed_model


def test_siliconflow_embedding_fallback_without_api_key_uses_hash_for_query_mode() -> None:
    embedding = SiliconFlowEmbedding(api_key="", allow_runtime_fallback=True)

    vector = embedding._get_text_embedding("spark plug gap")

    assert len(vector) == ManualHashEmbedding().dimensions


def test_siliconflow_embedding_no_runtime_fallback_raises() -> None:
    embedding = SiliconFlowEmbedding(api_key="", allow_runtime_fallback=False)

    with pytest.raises(RuntimeError, match="runtime fallback"):
        embedding._get_text_embedding("spark plug gap")


def test_default_embed_model_returns_single_resolved_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "siliconflow_api_key", None)

    embedding = _default_embed_model()

    assert isinstance(embedding, ManualHashEmbedding)
    assert embedding.model_name == "manual-local-hash-embedding"
