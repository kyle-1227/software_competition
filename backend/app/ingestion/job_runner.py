from __future__ import annotations

from app.ingestion.pdf_pipeline import PdfIngestionPipeline
from app.ingestion.schemas import IngestionJobContext, StagingResult
from app.ingestion.staging import KnowledgeStagingWriter
from app.ingestion.verifier import IngestionVerifier


class IngestionJobRunner:
    def __init__(
        self,
        pdf_pipeline: PdfIngestionPipeline | None = None,
        staging_writer: KnowledgeStagingWriter | None = None,
        verifier: IngestionVerifier | None = None,
    ) -> None:
        self.pdf_pipeline = pdf_pipeline or PdfIngestionPipeline()
        self.staging_writer = staging_writer or KnowledgeStagingWriter()
        self.verifier = verifier or IngestionVerifier()

    def run_pdf(self, context: IngestionJobContext) -> StagingResult:
        parsed = self.pdf_pipeline.parse(context)
        self.verifier.verify(parsed)
        return self.staging_writer.stage(parsed)
