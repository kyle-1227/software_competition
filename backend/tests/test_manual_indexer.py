from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.manual_indexer import (
    REQUIRED_DOCUMENT_METADATA_KEYS,
    ManualIndexer,
    load_manual_documents,
)


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_load_manual_documents_preserves_frontend_metadata(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p3:r6",
                "manual_id": "motorcycle_engine_manual",
                "source": "manual.xlsx",
                "page": "3",
                "chapter": "chapter one",
                "section": "section 1.4",
                "text": "section 1.4\nstep text",
                "keywords": ["pressure", "spark plug"],
                "block_type": "measure steps",
                "metadata": {
                    "chapter": "stale chapter",
                    "section": "stale section",
                    "block_type": "stale block type",
                    "source_type": "excel_manual_chunks",
                },
            }
        ],
    )

    documents = load_manual_documents(chunks_path)

    assert len(documents) == 1
    document = documents[0]
    assert document.id_ == "manual:p3:r6"
    assert document.text == "section 1.4\nstep text"
    assert document.metadata == {
        "chapter": "chapter one",
        "section": "section 1.4",
        "block_type": "measure steps",
        "source_type": "excel_manual_chunks",
        "source": "manual.xlsx",
        "page": 3,
        "chunk_id": "manual:p3:r6",
        "manual_id": "motorcycle_engine_manual",
        "keywords": ["pressure", "spark plug"],
    }
    assert set(REQUIRED_DOCUMENT_METADATA_KEYS).issubset(document.metadata)


def test_default_manual_documents_are_built_from_generated_jsonl() -> None:
    documents = load_manual_documents(
        ROOT_DIR / "data" / "processed" / "manual_chunks.jsonl"
    )

    assert len(documents) == 118
    assert all(
        set(REQUIRED_DOCUMENT_METADATA_KEYS).issubset(document.metadata)
        for document in documents
    )
    assert documents[0].id_ == documents[0].metadata["chunk_id"]


def test_manual_indexer_exposes_document_loader(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p1:r2",
                "source": "manual.xlsx",
                "page": 1,
                "chapter": "contents",
                "section": "toc",
                "text": "toc text",
                "block_type": "toc",
            }
        ],
    )

    assert ManualIndexer().load_documents(chunks_path)[0].metadata["chunk_id"] == (
        "manual:p1:r2"
    )


def test_load_manual_documents_rejects_missing_required_metadata(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p3:r6",
                "source": "manual.xlsx",
                "page": 3,
                "chapter": "chapter one",
                "section": "section 1.4",
                "text": "section 1.4\nstep text",
            }
        ],
    )

    with pytest.raises(ValueError, match="block_type"):
        load_manual_documents(chunks_path)


def _write_jsonl(path: Path, chunks: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
