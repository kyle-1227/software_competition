from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.knowledge.base import PsycopgRepository, jsonb
from app.services.tracing.serializers import sanitize_trace_dict


class EmbeddingRepository(PsycopgRepository):
    def upsert_embedding(
        self,
        *,
        chunk_id: str,
        provider: str,
        model: str,
        embedding: list[float],
        embedding_id: str | None = None,
        dimensions: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        row_id = embedding_id or str(uuid4())
        dims = dimensions if dimensions is not None else len(embedding)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO embeddings (
                        embedding_id, chunk_id, provider, model, dimensions,
                        embedding, metadata_json
                    )
                    VALUES (
                        %(embedding_id)s, %(chunk_id)s, %(provider)s,
                        %(model)s, %(dimensions)s, %(embedding)s,
                        %(metadata_json)s
                    )
                    ON CONFLICT (chunk_id, provider, model) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        dimensions = EXCLUDED.dimensions,
                        metadata_json = EXCLUDED.metadata_json
                    RETURNING embedding_id
                    """,
                    {
                        "embedding_id": row_id,
                        "chunk_id": chunk_id,
                        "provider": provider,
                        "model": model,
                        "dimensions": dims,
                        "embedding": embedding,
                        "metadata_json": jsonb(sanitize_trace_dict(metadata or {})),
                    },
                )
                returned = cur.fetchone()
        return str(returned[0]) if returned else row_id
