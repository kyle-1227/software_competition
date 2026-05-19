from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.knowledge.base import PsycopgRepository, jsonb
from app.services.tracing.serializers import sanitize_trace_dict


class DocumentRepository(PsycopgRepository):
    def create_ingestion_job(
        self,
        *,
        source_uri: str,
        document_id: str | None = None,
        document_version_id: str | None = None,
        active_on_success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        job_id = str(uuid4())
        active = self._active_value(active_on_success)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ingestion_jobs (
                        job_id, document_id, document_version_id, source_uri, status,
                        active_on_success, metadata_json
                    )
                    VALUES (%s, %s, %s, %s, 'queued', %s, %s)
                    """,
                    (
                        job_id,
                        document_id,
                        document_version_id,
                        source_uri,
                        active,
                        jsonb(sanitize_trace_dict(metadata or {})),
                    ),
                )
        return job_id

    def create_document_version(
        self,
        *,
        document_version_id: str,
        document_id: str,
        source_uri: str,
        version: int = 1,
        sha256: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        status: str = "pending",
        active: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, document_id, version, source_uri,
                        sha256, parser_name, parser_version, status, active,
                        metadata_json
                    )
                    VALUES (
                        %(document_version_id)s, %(document_id)s, %(version)s,
                        %(source_uri)s, %(sha256)s, %(parser_name)s,
                        %(parser_version)s, %(status)s, %(active)s,
                        %(metadata_json)s
                    )
                    ON CONFLICT (document_version_id) DO UPDATE SET
                        source_uri = EXCLUDED.source_uri,
                        sha256 = EXCLUDED.sha256,
                        parser_name = EXCLUDED.parser_name,
                        parser_version = EXCLUDED.parser_version,
                        status = EXCLUDED.status,
                        active = EXCLUDED.active,
                        metadata_json = EXCLUDED.metadata_json
                    """,
                    {
                        "document_version_id": document_version_id,
                        "document_id": document_id,
                        "version": version,
                        "source_uri": source_uri,
                        "sha256": sha256,
                        "parser_name": parser_name,
                        "parser_version": parser_version,
                        "status": status,
                        "active": self._active_value(active),
                        "metadata_json": jsonb(sanitize_trace_dict(metadata or {})),
                    },
                )

    def upsert_document(
        self,
        *,
        document_id: str,
        source_uri: str,
        source_type: str = "manual",
        title: str | None = None,
        mime_type: str | None = None,
        sha256: str | None = None,
        device_name: str | None = None,
        device_model: str | None = None,
        status: str = "pending",
        active: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        params = self.document_params(
            document_id=document_id,
            source_uri=source_uri,
            source_type=source_type,
            title=title,
            mime_type=mime_type,
            sha256=sha256,
            device_name=device_name,
            device_model=device_model,
            status=status,
            active=active,
            metadata=metadata,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (
                        document_id, source_uri, source_type, title, mime_type,
                        sha256, device_name, device_model, status, active,
                        metadata_json
                    )
                    VALUES (%(document_id)s, %(source_uri)s, %(source_type)s,
                            %(title)s, %(mime_type)s, %(sha256)s,
                            %(device_name)s, %(device_model)s, %(status)s,
                            %(active)s, %(metadata_json)s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        source_uri = EXCLUDED.source_uri,
                        source_type = EXCLUDED.source_type,
                        title = EXCLUDED.title,
                        mime_type = EXCLUDED.mime_type,
                        sha256 = EXCLUDED.sha256,
                        device_name = EXCLUDED.device_name,
                        device_model = EXCLUDED.device_model,
                        status = EXCLUDED.status,
                        active = EXCLUDED.active,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now()
                    """,
                    {**params, "metadata_json": jsonb(params["metadata_json"])},
                )

    @staticmethod
    def document_params(
        *,
        document_id: str,
        source_uri: str,
        source_type: str = "manual",
        title: str | None = None,
        mime_type: str | None = None,
        sha256: str | None = None,
        device_name: str | None = None,
        device_model: str | None = None,
        status: str = "pending",
        active: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "source_uri": source_uri,
            "source_type": source_type,
            "title": title,
            "mime_type": mime_type,
            "sha256": sha256,
            "device_name": device_name,
            "device_model": device_model,
            "status": status,
            "active": DocumentRepository._active_value(active),
            "metadata_json": sanitize_trace_dict(metadata or {}),
        }

    @staticmethod
    def _active_value(value: bool | None) -> bool:
        if value is None:
            return bool(getattr(settings, "knowledge_active_by_default", False))
        return bool(value)
