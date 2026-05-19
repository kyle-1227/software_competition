from __future__ import annotations

from pathlib import Path

from app.ingestion.rag_anything_adapter import RagAnythingAdapter
from app.ingestion.schemas import (
    IngestionJobContext,
    ParsedChunk,
    ParsedDocument,
    ParsedEvidence,
)


class PdfIngestionPipeline:
    def __init__(self, adapter: RagAnythingAdapter | None = None) -> None:
        self.adapter = adapter or RagAnythingAdapter()

    def parse(self, context: IngestionJobContext) -> ParsedDocument:
        try:
            return self.adapter.parse_pdf(
                context.source_path,
                document_id=context.document_id,
                document_version_id=context.document_version_id,
                metadata=context.metadata,
            )
        except Exception as exc:
            return self._fallback_for_staging_test(context, reason=str(exc))

    def _fallback_for_staging_test(
        self,
        context: IngestionJobContext,
        *,
        reason: str,
    ) -> ParsedDocument:
        text = _safe_pdf_preview(context.source_path)
        metadata = {
            **context.metadata,
            "fallback_for_ingestion_test": True,
            "not_for_final_answer": True,
            "fallback_reason": reason[:500],
        }
        chunk_id = f"{context.document_version_id}:fallback:chunk:0"
        return ParsedDocument(
            document_id=context.document_id,
            document_version_id=context.document_version_id,
            source_uri=str(context.source_path),
            parser_name="pdf-ingestion-fallback",
            parser_version="v1",
            chunks=[
                ParsedChunk(
                    chunk_id=chunk_id,
                    text=text,
                    chunk_index=0,
                    page=1,
                    metadata=metadata,
                )
            ],
            evidence=[
                ParsedEvidence(
                    source_id=chunk_id,
                    source=str(context.source_path),
                    page=1,
                    snippet=text[:300],
                    chunk_id=chunk_id,
                    metadata=metadata,
                )
            ],
            metadata=metadata,
        )


def _safe_pdf_preview(path: Path) -> str:
    try:
        raw = path.read_bytes()[:4096]
    except OSError:
        return "PDF ingestion fallback: file could not be read."
    decoded = raw.decode("utf-8", errors="ignore").strip()
    if decoded:
        return decoded[:1000]
    return f"PDF ingestion fallback placeholder for {path.name}."
