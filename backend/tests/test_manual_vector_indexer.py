from __future__ import annotations

import json
from pathlib import Path

from app.services.manual_vector_indexer import (
    MANUAL_INDEX_ID,
    REQUIRED_DOCUMENT_METADATA_KEYS,
    build_manual_vector_index,
    get_manual_vector_retriever,
)


def test_build_manual_vector_index_persists_and_preserves_metadata(
    tmp_path: Path,
) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p3:r6",
                "manual_id": "motorcycle_engine_manual",
                "source": "manual.xlsx",
                "page": 3,
                "chapter": "spark plug",
                "section": "measure compression pressure",
                "text": "measure compression pressure with a pressure gauge",
                "keywords": ["compression pressure", "pressure gauge"],
                "block_type": "measure steps",
                "metadata": {"source_type": "excel_manual_chunks"},
            },
            {
                "chunk_id": "manual:p15:r32",
                "manual_id": "motorcycle_engine_manual",
                "source": "manual.xlsx",
                "page": 15,
                "chapter": "valve",
                "section": "valve clearance",
                "text": "intake valve clearance and exhaust valve clearance",
                "keywords": ["valve clearance"],
                "block_type": "technical spec",
                "metadata": {"source_type": "excel_manual_chunks"},
            },
        ],
    )

    result = build_manual_vector_index(chunks_path=chunks_path, index_dir=index_dir)

    assert result.document_count == 2
    assert result.index_id == MANUAL_INDEX_ID
    assert index_dir.exists()
    assert (index_dir / "docstore.json").exists()
    assert (index_dir / "index_store.json").exists()
    assert (index_dir / "default__vector_store.json").exists()

    retriever = get_manual_vector_retriever(index_dir=index_dir, similarity_top_k=1)
    nodes = retriever.retrieve("compression pressure")

    assert nodes
    metadata = nodes[0].node.metadata
    assert set(REQUIRED_DOCUMENT_METADATA_KEYS).issubset(metadata)
    assert metadata["chunk_id"] == "manual:p3:r6"
    assert metadata["source"] == "manual.xlsx"
    assert metadata["page"] == 3
    assert metadata["chapter"] == "spark plug"
    assert metadata["section"] == "measure compression pressure"
    assert metadata["block_type"] == "measure steps"


def _write_jsonl(path: Path, chunks: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
