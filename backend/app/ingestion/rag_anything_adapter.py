from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from app.core.config import settings
from app.ingestion.schemas import ParsedAsset, ParsedChunk, ParsedDocument, ParsedEvidence


class RagAnythingAdapter:
    """Adapter for a multimodal parsing service.

    RAG-Anything is intentionally limited to returning structured knowledge and
    evidence. It must not produce final repair or business answers.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.rag_anything_base_url or "").rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.rag_anything_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def parse_pdf(
        self,
        path: Path,
        *,
        document_id: str,
        document_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ParsedDocument:
        if not self.configured:
            raise RuntimeError("RAG-Anything base URL is not configured")

        with path.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/parse/pdf",
                files={"file": (path.name, handle, "application/pdf")},
                data={
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                },
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
        return self._parsed_document_from_payload(
            payload,
            source_uri=str(path),
            document_id=document_id,
            document_version_id=document_version_id,
            metadata=metadata or {},
        )

    def _parsed_document_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source_uri: str,
        document_id: str,
        document_version_id: str,
        metadata: dict[str, Any],
    ) -> ParsedDocument:
        chunks = [
            ParsedChunk(
                chunk_id=str(item.get("chunk_id") or f"{document_version_id}:chunk:{idx}"),
                text=str(item.get("text") or item.get("content") or ""),
                chunk_index=int(item.get("chunk_index") or idx),
                page=_optional_int(item.get("page")),
                asset_id=item.get("asset_id"),
                token_count=_optional_int(item.get("token_count")),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for idx, item in enumerate(payload.get("chunks") or [])
            if isinstance(item, dict)
        ]
        evidence = [
            ParsedEvidence(
                source_id=str(
                    item.get("source_id")
                    or item.get("chunk_id")
                    or f"{document_version_id}:evidence:{idx}"
                ),
                source=str(item.get("source") or source_uri),
                snippet=str(item.get("snippet") or item.get("text") or ""),
                page=_optional_int(item.get("page")),
                score=_optional_float(item.get("score")),
                chunk_id=item.get("chunk_id"),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for item in payload.get("evidence", []) or []
            if isinstance(item, dict)
        ]
        assets = [
            ParsedAsset(
                asset_id=str(item.get("asset_id") or f"{document_version_id}:asset:{idx}"),
                asset_type=item.get("asset_type") or "page",
                uri=str(item.get("uri") or source_uri),
                mime_type=item.get("mime_type"),
                page_number=_optional_int(item.get("page_number")),
                width=_optional_int(item.get("width")),
                height=_optional_int(item.get("height")),
                metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            )
            for idx, item in enumerate(payload.get("assets") or [])
            if isinstance(item, dict)
        ]
        return ParsedDocument(
            document_id=document_id,
            document_version_id=document_version_id,
            source_uri=source_uri,
            parser_name=str(payload.get("parser_name") or "rag-anything"),
            parser_version=payload.get("parser_version"),
            assets=assets,
            chunks=chunks,
            evidence=evidence,
            metadata={**metadata, "rag_anything": True},
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
