from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from app.db.base import Base

try:  # pragma: no cover - exercised when pgvector is installed.
    from pgvector.sqlalchemy import Vector as PgVector
except Exception:  # pragma: no cover - local test env may not have pgvector yet.
    PgVector = None


class VectorType(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"


def vector_column_type():
    if PgVector is not None:
        return PgVector()
    return VectorType()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'archived')",
            name="chk_documents_status",
        ),
        Index("idx_documents_source_uri", "source_uri"),
        Index("idx_documents_sha256", "sha256"),
        Index("idx_documents_active", "active"),
    )

    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    title: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    device_name: Mapped[str | None] = mapped_column(Text)
    device_model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    assets: Mapped[list["Asset"]] = relationship(back_populates="document")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document")
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_versions_version"),
        Index("idx_document_versions_document_id", "document_id"),
        Index("idx_document_versions_sha256", "sha256"),
        Index("idx_document_versions_active", "active"),
    )

    document_version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text)
    parser_name: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="versions")


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        Index("idx_assets_document_id", "document_id"),
        Index("idx_assets_asset_type", "asset_type"),
        Index("idx_assets_sha256", "sha256"),
    )

    asset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    document_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_versions.document_version_id", ondelete="SET NULL")
    )
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="assets")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="asset")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("idx_chunks_document_id", "document_id"),
        Index("idx_chunks_asset_id", "asset_id"),
        Index("idx_chunks_active", "active"),
    )

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("assets.asset_id", ondelete="SET NULL")
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    asset: Mapped[Asset | None] = relationship(back_populates="chunks")
    embeddings: Mapped[list["Embedding"]] = relationship(back_populates="chunk")


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "provider", "model", name="uq_embeddings_chunk_model"),
        Index("idx_embeddings_chunk_id", "chunk_id"),
        Index("idx_embeddings_model", "provider", "model"),
    )

    embedding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chunks.chunk_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(vector_column_type(), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunk: Mapped[Chunk] = relationship(back_populates="embeddings")


class EvidenceLedgerEntry(Base):
    __tablename__ = "evidence_ledger"
    __table_args__ = (
        Index("idx_evidence_ledger_trace_id", "trace_id"),
        Index("idx_evidence_ledger_runtime_request_id", "runtime_request_id"),
        Index("idx_evidence_ledger_chunk_id", "chunk_id"),
        Index("idx_evidence_ledger_document_id", "document_id"),
    )

    evidence_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    runtime_request_id: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("documents.document_id", ondelete="SET NULL")
    )
    document_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_versions.document_version_id", ondelete="SET NULL")
    )
    chunk_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("chunks.chunk_id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    retrieval_method: Mapped[str | None] = mapped_column(Text)
    is_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="chk_ingestion_jobs_status",
        ),
        Index("idx_ingestion_jobs_document_id", "document_id"),
        Index("idx_ingestion_jobs_status", "status"),
        Index("idx_ingestion_jobs_source_uri", "source_uri"),
    )

    job_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("documents.document_id", ondelete="SET NULL")
    )
    document_version_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_versions.document_version_id", ondelete="SET NULL")
    )
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    active_on_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
