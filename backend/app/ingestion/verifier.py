from __future__ import annotations

from app.ingestion.schemas import ParsedDocument


class IngestionVerifier:
    def verify(self, parsed: ParsedDocument) -> None:
        if not parsed.chunks:
            raise ValueError("Parsed document contains no chunks")
        for chunk in parsed.chunks:
            if not chunk.text.strip():
                raise ValueError(f"Parsed chunk {chunk.chunk_id} is empty")
