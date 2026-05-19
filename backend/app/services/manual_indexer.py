from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from llama_index.core import Document

from app.core.config import settings
from app.db.session import database_url
from app.ingestion.job_runner import IngestionJobRunner
from app.ingestion.pdf_pipeline import PdfIngestionPipeline
from app.ingestion.schemas import IngestionJobContext
from app.ingestion.staging import KnowledgeStagingWriter
from app.knowledge.repository import KnowledgeRepository
from app.schemas.manual import ManualRegisterRequest, ManualRegisterResponse


DEFAULT_CHUNKS_PATH = settings.data_path / "processed" / "manual_chunks.jsonl"
REQUIRED_DOCUMENT_METADATA_KEYS = (
    "source",
    "page",
    "chapter",
    "section",
    "chunk_id",
    "block_type",
)


class ManualIndexer:
    def __init__(
        self,
        knowledge_repository: KnowledgeRepository | None = None,
        pdf_pipeline: PdfIngestionPipeline | None = None,
    ) -> None:
        self.knowledge_repository = knowledge_repository
        self.pdf_pipeline = pdf_pipeline

    def load_documents(self, chunks_path: Path | None = None) -> list[Document]:
        return load_manual_documents(chunks_path)

    async def register_manual(
        self, payload: ManualRegisterRequest
    ) -> ManualRegisterResponse:
        file_path = Path(payload.file_path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.exists():
            raise FileNotFoundError(f"Manual file not found: {file_path}")

        resolved = file_path.resolve()
        document_id = str(uuid4())
        document_version_id = str(uuid4())
        job_id = str(uuid4())
        sha256 = _file_sha256(resolved)
        repository = self._repository()

        if repository is not None:
            repository.initialize(include_trace_schema=True)
            repository.upsert_document(
                document_id=document_id,
                source_uri=str(resolved),
                source_type="manual",
                title=resolved.name,
                mime_type="application/pdf",
                sha256=sha256,
                device_name=payload.device_name,
                device_model=payload.device_model,
                status="processing",
                active=False,
                metadata={"registered_by": "manual_indexer"},
            )
            repository.create_document_version(
                document_version_id=document_version_id,
                document_id=document_id,
                source_uri=str(resolved),
                version=1,
                sha256=sha256,
                parser_name="rag-anything",
                status="processing",
                active=False,
                metadata={"registered_by": "manual_indexer"},
            )
            job_id = repository.create_ingestion_job(
                source_uri=str(resolved),
                document_id=document_id,
                document_version_id=document_version_id,
                active_on_success=False,
                metadata={
                    "device_name": payload.device_name,
                    "device_model": payload.device_model,
                },
            )

        context = IngestionJobContext(
            job_id=job_id,
            document_id=document_id,
            document_version_id=document_version_id,
            source_path=resolved,
            device_name=payload.device_name,
            device_model=payload.device_model,
            metadata={
                "device_name": payload.device_name,
                "device_model": payload.device_model,
                "sha256": sha256,
            },
        )
        runner = IngestionJobRunner(
            pdf_pipeline=self.pdf_pipeline or PdfIngestionPipeline(),
            staging_writer=KnowledgeStagingWriter(repository),
        )
        staging = runner.run_pdf(context)
        status = "staged" if staging.staged else f"staging_skipped:{staging.skipped_reason}"

        return ManualRegisterResponse(
            manual_id=document_id,
            document_id=document_id,
            document_version_id=document_version_id,
            job_id=job_id,
            file_path=str(resolved),
            page_count=None,
            status=status,
            next_step=(
                "Review staged chunks/evidence, then explicitly activate the "
                "document version before it can affect retrieval."
            ),
        )

    def _repository(self) -> KnowledgeRepository | None:
        if self.knowledge_repository is not None:
            return self.knowledge_repository
        db_url = database_url()
        return KnowledgeRepository(db_url) if db_url else None


def load_manual_documents(chunks_path: Path | None = None) -> list[Document]:
    path = chunks_path or DEFAULT_CHUNKS_PATH
    if not path.exists():
        raise FileNotFoundError(f"Manual chunks JSONL not found: {path}")

    documents: list[Document] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            chunk = _parse_chunk_line(line, path, line_number)
            documents.append(_chunk_to_document(chunk, path, line_number))
    return documents


def _parse_chunk_line(line: str, path: Path, line_number: int) -> dict[str, Any]:
    try:
        chunk = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in manual chunks file {path} at line {line_number}: {exc.msg}"
        ) from exc

    if not isinstance(chunk, dict):
        raise ValueError(
            f"Manual chunk at {path}:{line_number} must be a JSON object."
        )
    return chunk


def _chunk_to_document(chunk: dict[str, Any], path: Path, line_number: int) -> Document:
    text = str(chunk.get("text") or chunk.get("content") or "").strip()
    if not text:
        raise ValueError(f"Manual chunk at {path}:{line_number} is missing text.")

    metadata = _document_metadata(chunk)
    missing_keys = [
        key
        for key in REQUIRED_DOCUMENT_METADATA_KEYS
        if metadata.get(key) in (None, "")
    ]
    if missing_keys:
        raise ValueError(
            f"Manual chunk at {path}:{line_number} is missing required metadata: "
            + ", ".join(missing_keys)
        )

    return Document(text=text, metadata=metadata, id_=str(metadata["chunk_id"]))


def _document_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    chunk_metadata = (
        chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    )
    metadata = dict(chunk_metadata)

    metadata.update(
        {
            "source": chunk.get("source") or metadata.get("source"),
            "page": _optional_int(chunk.get("page") or metadata.get("page")),
            "chapter": chunk.get("chapter") or metadata.get("chapter"),
            "section": chunk.get("section") or metadata.get("section"),
            "chunk_id": chunk.get("chunk_id") or metadata.get("chunk_id"),
            "block_type": chunk.get("block_type") or metadata.get("block_type"),
        }
    )

    if chunk.get("manual_id") is not None:
        metadata["manual_id"] = chunk["manual_id"]
    if chunk.get("keywords") is not None:
        metadata["keywords"] = chunk["keywords"]

    return metadata


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
