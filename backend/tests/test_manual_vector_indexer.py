from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.embeddings.siliconflow_embedding import SiliconFlowEmbedding
from app.services.manual_vector_indexer import (
    MANUAL_INDEX_ID,
    ManualHashEmbedding,
    REQUIRED_DOCUMENT_METADATA_KEYS,
    build_manual_vector_index,
    get_manual_vector_retriever,
    load_manual_vector_index,
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
    assert (index_dir / "index_meta.json").exists()

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


def test_index_meta_written(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    _write_jsonl(chunks_path, _sample_chunks())

    result = build_manual_vector_index(chunks_path=chunks_path, index_dir=index_dir)
    meta = json.loads((index_dir / "index_meta.json").read_text(encoding="utf-8"))

    assert meta["index_id"] == MANUAL_INDEX_ID
    assert meta["provider"] == result.provider
    assert meta["embedding_model"] == result.embedding_model
    assert meta["dimensions"] == result.dimensions
    assert meta["chunks_sha256"] == result.chunks_sha256


def test_load_index_rejects_embedding_model_mismatch(tmp_path: Path) -> None:
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    index_dir.mkdir(parents=True)
    _write_meta(index_dir, embedding_model="different-model", dimensions=384)

    with pytest.raises(RuntimeError, match="Please rebuild"):
        load_manual_vector_index(index_dir=index_dir, embed_model=ManualHashEmbedding())


def test_load_index_rejects_embedding_dimension_mismatch(tmp_path: Path) -> None:
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    index_dir.mkdir(parents=True)
    _write_meta(index_dir, embedding_model="manual-local-hash-embedding", dimensions=1024)

    with pytest.raises(RuntimeError, match="Embedding index mismatch"):
        load_manual_vector_index(index_dir=index_dir, embed_model=ManualHashEmbedding())


def test_legacy_index_without_meta_allows_loading_or_warns(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    _write_jsonl(chunks_path, _sample_chunks())
    build_manual_vector_index(chunks_path=chunks_path, index_dir=index_dir)
    (index_dir / "index_meta.json").unlink()

    index = load_manual_vector_index(index_dir=index_dir, embed_model=ManualHashEmbedding())

    assert index is not None


def test_no_runtime_fallback_during_index_build(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    index_dir = tmp_path / "indexes" / "manuals" / "motorcycle_engine"
    _write_jsonl(chunks_path, _sample_chunks())
    embedding = SiliconFlowEmbedding(
        api_key="test-key",
        model="BAAI/bge-large-zh-v1.5",
        allow_runtime_fallback=True,
    )
    embedding._client = _FailingEmbeddingClient()

    with pytest.raises(RuntimeError, match="runtime fallback disabled"):
        build_manual_vector_index(
            chunks_path=chunks_path,
            index_dir=index_dir,
            embed_model=embedding,
        )


def _write_jsonl(path: Path, chunks: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )


def _sample_chunks() -> list[dict[str, object]]:
    return [
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
        }
    ]


def _write_meta(
    index_dir: Path,
    *,
    embedding_model: str,
    dimensions: int,
    provider: str = "local",
) -> None:
    meta = {
        "chunks_path": str(index_dir / "manual_chunks.jsonl"),
        "index_dir": str(index_dir),
        "document_count": 1,
        "index_id": MANUAL_INDEX_ID,
        "provider": provider,
        "embedding_model": embedding_model,
        "dimensions": dimensions,
        "chunks_sha256": "sha",
        "metadata_keys": list(REQUIRED_DOCUMENT_METADATA_KEYS),
    }
    (index_dir / "index_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


class _FailingEmbeddingClient:
    embeddings = None

    def __init__(self) -> None:
        self.embeddings = self

    def create(self, **kwargs):
        del kwargs
        raise RuntimeError("primary failed")
