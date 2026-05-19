from __future__ import annotations

from app.knowledge.chunk_repository import ChunkRepository
from app.knowledge.document_repository import DocumentRepository
from app.knowledge.embedding_repository import EmbeddingRepository
from app.knowledge.evidence_ledger import EvidenceLedgerRepository
from app.services.knowledge.migrations import migrate_knowledge_schema
from app.services.tracing.migrations import migrate_trace_schema


class KnowledgeRepository:
    """Small facade for optional PostgreSQL-backed knowledge repositories."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.documents = DocumentRepository(database_url)
        self.chunks = ChunkRepository(database_url)
        self.embeddings = EmbeddingRepository(database_url)
        self.evidence_ledger = EvidenceLedgerRepository(database_url)

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

    def create_ingestion_job(self, **kwargs):
        return self.documents.create_ingestion_job(**kwargs)

    def upsert_document(self, **kwargs):
        return self.documents.upsert_document(**kwargs)

    def create_document_version(self, **kwargs):
        return self.documents.create_document_version(**kwargs)

    def record_evidence(self, *args, **kwargs):
        return self.evidence_ledger.record_evidence(*args, **kwargs)
