from __future__ import annotations

from typing import Any

from app.schemas.query import EvidenceItem
from app.services.knowledge.migrations import migrate_knowledge_schema
from app.services.tracing.migrations import migrate_trace_schema
from app.knowledge.document_repository import DocumentRepository
from app.knowledge.evidence_ledger import EvidenceLedgerRepository


class PostgreSQLKnowledgeRepository:
    """PostgreSQL fact-source repository using the existing psycopg driver."""

    def __init__(self, database_url: str | None = None) -> None:
        self.documents = DocumentRepository(database_url)
        self.evidence_ledger = EvidenceLedgerRepository(database_url)
        self.database_url = self.documents.database_url

    def initialize(self, *, include_trace_schema: bool = True) -> None:
        with self.documents._connect(autocommit=False) as conn:
            try:
                if include_trace_schema:
                    migrate_trace_schema(conn)
                migrate_knowledge_schema(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_ingestion_job(
        self,
        *,
        source_uri: str,
        document_id: str | None = None,
        document_version_id: str | None = None,
        active_on_success: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.documents.create_ingestion_job(
            source_uri=source_uri,
            document_id=document_id,
            document_version_id=document_version_id,
            active_on_success=active_on_success,
            metadata=metadata,
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
        return self.documents.upsert_document(
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

    def create_document_version(self, **kwargs) -> None:
        return self.documents.create_document_version(**kwargs)

    def record_evidence(
        self,
        evidence: list[EvidenceItem | dict[str, Any]],
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        runtime_request_id: str | None = None,
        document_version_id: str | None = None,
        retrieval_method: str | None = None,
    ) -> list[str]:
        return self.evidence_ledger.record_evidence(
            evidence,
            trace_id=trace_id,
            run_id=run_id,
            runtime_request_id=runtime_request_id,
            document_version_id=document_version_id,
            retrieval_method=retrieval_method,
        )

    def _connect(self, *, autocommit: bool = True):
        return self.documents._connect(autocommit=autocommit)

    @staticmethod
    def _document_params(
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
        return DocumentRepository.document_params(
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

    @staticmethod
    def _evidence_row(
        item: EvidenceItem | dict[str, Any],
        *,
        trace_id: str | None,
        run_id: str | None,
        runtime_request_id: str | None,
        document_version_id: str | None = None,
        retrieval_method: str | None,
    ) -> dict[str, Any]:
        return EvidenceLedgerRepository.evidence_row(
            item,
            trace_id=trace_id,
            run_id=run_id,
            runtime_request_id=runtime_request_id,
            document_version_id=document_version_id,
            retrieval_method=retrieval_method,
        )
