from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class IngestionJobContext(BaseModel):
    job_id: str
    document_id: str
    document_version_id: str
    source_path: Path
    device_name: str | None = None
    device_model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class ParsedAsset(BaseModel):
    asset_id: str
    asset_type: Literal["pdf", "image", "table", "diagram", "page"] = "page"
    uri: str
    mime_type: str | None = None
    page_number: int | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedChunk(BaseModel):
    chunk_id: str
    text: str
    chunk_index: int
    page: int | None = None
    asset_id: str | None = None
    token_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedEvidence(BaseModel):
    source_id: str
    source: str
    snippet: str
    page: int | None = None
    score: float | None = None
    chunk_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    document_id: str
    document_version_id: str
    source_uri: str
    parser_name: str
    parser_version: str | None = None
    assets: list[ParsedAsset] = Field(default_factory=list)
    chunks: list[ParsedChunk] = Field(default_factory=list)
    evidence: list[ParsedEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StagingResult(BaseModel):
    staged: bool
    skipped_reason: str | None = None
    document_id: str
    document_version_id: str
    chunk_count: int = 0
    evidence_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
