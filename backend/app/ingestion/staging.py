from __future__ import annotations

from app.ingestion.schemas import ParsedDocument, StagingResult
from app.knowledge.repository import KnowledgeRepository
from app.schemas.query import EvidenceItem


class KnowledgeStagingWriter:
    def __init__(self, repository: KnowledgeRepository | None = None) -> None:
        self.repository = repository

    def stage(self, parsed: ParsedDocument) -> StagingResult:
        if self.repository is None:
            return StagingResult(
                staged=False,
                skipped_reason="database_not_configured",
                document_id=parsed.document_id,
                document_version_id=parsed.document_version_id,
                chunk_count=len(parsed.chunks),
                evidence_count=len(parsed.evidence),
                metadata={"staging": "skipped"},
            )

        for chunk in parsed.chunks:
            self.repository.chunks.upsert_chunk(
                chunk_id=chunk.chunk_id,
                document_id=parsed.document_id,
                document_version_id=parsed.document_version_id,
                asset_id=chunk.asset_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                page=chunk.page,
                token_count=chunk.token_count,
                active=False,
                metadata={
                    **chunk.metadata,
                    "document_version_id": parsed.document_version_id,
                    "parser_name": parsed.parser_name,
                },
            )

        evidence_items = [
            EvidenceItem(
                source=item.source,
                page=item.page,
                snippet=item.snippet,
                score=item.score,
                metadata={
                    **item.metadata,
                    "source_id": item.source_id,
                    "document_id": parsed.document_id,
                    "document_version_id": parsed.document_version_id,
                    "chunk_id": item.chunk_id,
                },
            )
            for item in parsed.evidence
        ]
        self.repository.evidence_ledger.record_evidence(
            evidence_items,
            document_version_id=parsed.document_version_id,
            retrieval_method=parsed.parser_name,
        )
        return StagingResult(
            staged=True,
            document_id=parsed.document_id,
            document_version_id=parsed.document_version_id,
            chunk_count=len(parsed.chunks),
            evidence_count=len(parsed.evidence),
            metadata={"staging": "postgres"},
        )
