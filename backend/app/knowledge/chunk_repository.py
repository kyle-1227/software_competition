from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.knowledge.base import PsycopgRepository, jsonb
from app.services.tracing.serializers import sanitize_trace_dict


class ChunkRepository(PsycopgRepository):
    def upsert_chunk(
        self,
        *,
        chunk_id: str,
        document_id: str,
        chunk_index: int,
        text: str,
        document_version_id: str | None = None,
        asset_id: str | None = None,
        page: int | None = None,
        token_count: int | None = None,
        active: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        active_value = (
            bool(getattr(settings, "knowledge_active_by_default", False))
            if active is None
            else bool(active)
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, document_version_id, asset_id,
                        chunk_index, text, page, token_count, active,
                        metadata_json
                    )
                    VALUES (
                        %(chunk_id)s, %(document_id)s, %(document_version_id)s,
                        %(asset_id)s, %(chunk_index)s, %(text)s, %(page)s,
                        %(token_count)s, %(active)s, %(metadata_json)s
                    )
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        document_id = EXCLUDED.document_id,
                        document_version_id = EXCLUDED.document_version_id,
                        asset_id = EXCLUDED.asset_id,
                        chunk_index = EXCLUDED.chunk_index,
                        text = EXCLUDED.text,
                        page = EXCLUDED.page,
                        token_count = EXCLUDED.token_count,
                        active = EXCLUDED.active,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now()
                    """,
                    {
                        "chunk_id": chunk_id,
                        "document_id": document_id,
                        "document_version_id": document_version_id,
                        "asset_id": asset_id,
                        "chunk_index": chunk_index,
                        "text": text,
                        "page": page,
                        "token_count": token_count,
                        "active": active_value,
                        "metadata_json": jsonb(sanitize_trace_dict(metadata or {})),
                    },
                )
