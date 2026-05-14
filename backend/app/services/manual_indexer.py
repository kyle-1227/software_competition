from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from llama_index.core import Document

from app.core.config import settings
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
    def load_documents(self, chunks_path: Path | None = None) -> list[Document]:
        return load_manual_documents(chunks_path)

    async def register_manual(
        self, payload: ManualRegisterRequest
    ) -> ManualRegisterResponse:
        # MVP keeps registration deterministic. Next step: parse PDF pages,
        # chunk text, and build a persisted LlamaIndex index under data/indexes.
        file_path = Path(payload.file_path)
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        if not file_path.exists():
            raise FileNotFoundError(f"未找到维修手册文件：{file_path}")

        return ManualRegisterResponse(
            manual_id=str(uuid4()),
            file_path=str(file_path.resolve()),
            page_count=None,
            status="已注册，等待索引构建",
            next_step="下一步接入 PDF 解析、文本分块和 LlamaIndex 索引构建流程。",
        )


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
