from __future__ import annotations

from app.ingestion.schemas import IngestionJobContext, ParsedDocument


class ImageIngestionPipeline:
    def parse(self, context: IngestionJobContext) -> ParsedDocument:
        raise NotImplementedError("Image ingestion adapter interface is reserved for RAG-Anything")
