from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.pdf_pipeline import PdfIngestionPipeline
from app.ingestion.schemas import (
    IngestionJobContext,
    ParsedChunk,
    ParsedDocument,
    ParsedEvidence,
)
from app.ingestion.staging import KnowledgeStagingWriter
from app.services.manual_indexer import ManualIndexer
from app.schemas.manual import ManualRegisterRequest


def test_pdf_pipeline_fallback_is_only_for_staging_tests(tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4\nmanual test content")
    context = IngestionJobContext(
        job_id="job-1",
        document_id="doc-1",
        document_version_id="ver-1",
        source_path=pdf,
    )

    parsed = PdfIngestionPipeline().parse(context)

    assert parsed.parser_name == "pdf-ingestion-fallback"
    assert parsed.chunks
    assert parsed.evidence
    assert parsed.metadata["fallback_for_ingestion_test"] is True
    assert parsed.metadata["not_for_final_answer"] is True
    assert parsed.evidence[0].metadata["not_for_final_answer"] is True


def test_staging_writer_writes_chunks_and_evidence() -> None:
    repo = _FakeKnowledgeRepository()
    parsed = ParsedDocument(
        document_id="doc-1",
        document_version_id="ver-1",
        source_uri="manual.pdf",
        parser_name="rag-anything",
        chunks=[
            ParsedChunk(
                chunk_id="chunk-1",
                chunk_index=0,
                text="spark plug",
                page=3,
            )
        ],
        evidence=[
            ParsedEvidence(
                source_id="chunk-1",
                source="manual.pdf",
                snippet="spark plug",
                page=3,
                chunk_id="chunk-1",
            )
        ],
    )

    result = KnowledgeStagingWriter(repo).stage(parsed)

    assert result.staged is True
    assert repo.chunks.rows[0]["document_version_id"] == "ver-1"
    assert repo.evidence_ledger.rows[0]["document_version_id"] == "ver-1"
    evidence_item = repo.evidence_ledger.rows[0]["evidence"][0]
    assert evidence_item.metadata["source_id"] == "chunk-1"
    assert evidence_item.page == 3
    assert evidence_item.snippet == "spark plug"
    assert len(repo.evidence_ledger.rows) == 1


@pytest.mark.anyio
async def test_manual_register_creates_ingestion_job_and_stages_via_ledger(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4\nmanual test content")
    repo = _FakeKnowledgeRepository()

    response = await ManualIndexer(
        knowledge_repository=repo,
        pdf_pipeline=_FakePdfPipeline(),
    ).register_manual(ManualRegisterRequest(file_path=str(pdf), device_name="motor"))

    assert response.job_id == "job-from-repo"
    assert repo.initialized is True
    assert repo.documents_created[0]["document_id"] == response.document_id
    assert repo.versions_created[0]["document_version_id"] == response.document_version_id
    assert repo.jobs_created[0]["document_version_id"] == response.document_version_id
    assert repo.chunks.rows[0]["document_version_id"] == response.document_version_id
    assert repo.evidence_ledger.rows[0]["document_version_id"] == response.document_version_id
    evidence_item = repo.evidence_ledger.rows[0]["evidence"][0]
    assert evidence_item.metadata["source_id"] == "source-1"
    assert evidence_item.page == 7
    assert evidence_item.snippet == "verified evidence"


@pytest.mark.anyio
async def test_manual_register_returns_ingestion_ids_without_database(tmp_path: Path) -> None:
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4\nmanual test content")

    response = await ManualIndexer().register_manual(
        ManualRegisterRequest(file_path=str(pdf), device_name="motor")
    )

    assert response.document_id
    assert response.document_version_id
    assert response.job_id
    assert response.manual_id == response.document_id
    assert response.status == "staging_skipped:database_not_configured"


class _FakeChunks:
    def __init__(self) -> None:
        self.rows = []

    def upsert_chunk(self, **kwargs):
        self.rows.append(kwargs)


class _FakeEvidenceLedger:
    def __init__(self) -> None:
        self.rows = []

    def record_evidence(self, evidence, **kwargs):
        self.rows.append({"evidence": evidence, **kwargs})
        return ["evidence-1"]


class _FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.initialized = False
        self.documents_created = []
        self.versions_created = []
        self.jobs_created = []
        self.chunks = _FakeChunks()
        self.evidence_ledger = _FakeEvidenceLedger()

    def initialize(self, *, include_trace_schema: bool = True):
        del include_trace_schema
        self.initialized = True

    def upsert_document(self, **kwargs):
        self.documents_created.append(kwargs)

    def create_document_version(self, **kwargs):
        self.versions_created.append(kwargs)

    def create_ingestion_job(self, **kwargs):
        self.jobs_created.append(kwargs)
        return "job-from-repo"


class _FakePdfPipeline:
    def parse(self, context: IngestionJobContext) -> ParsedDocument:
        return ParsedDocument(
            document_id=context.document_id,
            document_version_id=context.document_version_id,
            source_uri=str(context.source_path),
            parser_name="rag-anything",
            chunks=[
                ParsedChunk(
                    chunk_id="chunk-1",
                    chunk_index=0,
                    text="verified evidence",
                    page=7,
                )
            ],
            evidence=[
                ParsedEvidence(
                    source_id="source-1",
                    source=str(context.source_path),
                    snippet="verified evidence",
                    page=7,
                    chunk_id="chunk-1",
                )
            ],
        )
