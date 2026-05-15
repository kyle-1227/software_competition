from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.embeddings import BaseEmbedding

from app.core.config import settings
from app.services.manual_indexer import (
    REQUIRED_DOCUMENT_METADATA_KEYS,
    load_manual_documents,
)


MANUAL_INDEX_ID = "manuals_motorcycle_engine"
DEFAULT_INDEX_DIR = settings.data_path / "indexes" / "manuals" / "motorcycle_engine"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[#./+\-][A-Za-z0-9]+)*|[\u4e00-\u9fff]{2,}")


class ManualHashEmbedding(BaseEmbedding):
    """Small deterministic local embedding for offline manual index builds."""

    dimensions: int = 384
    model_name: str = "manual-local-hash-embedding"

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._embed(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _tokenize_for_embedding(text)
        if not tokens:
            return self._fallback_vector(text)

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return self._fallback_vector(text)
        return [value / norm for value in vector]

    def _fallback_vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % self.dimensions
        vector[bucket] = 1.0
        return vector


@dataclass(frozen=True)
class ManualVectorIndexBuildResult:
    chunks_path: Path
    index_dir: Path
    document_count: int
    index_id: str
    provider: str
    embedding_model: str
    dimensions: int
    chunks_sha256: str
    metadata_keys: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunks_path": str(self.chunks_path),
            "index_dir": str(self.index_dir),
            "document_count": self.document_count,
            "index_id": self.index_id,
            "provider": self.provider,
            "embedding_model": self.embedding_model,
            "dimensions": self.dimensions,
            "chunks_sha256": self.chunks_sha256,
            "metadata_keys": list(self.metadata_keys),
        }

    @classmethod
    def from_index_meta(cls, meta: dict[str, Any]) -> ManualVectorIndexBuildResult:
        return cls(
            chunks_path=Path(meta["chunks_path"]),
            index_dir=Path(meta["index_dir"]),
            document_count=int(meta["document_count"]),
            index_id=str(meta["index_id"]),
            provider=str(meta.get("provider", "")),
            embedding_model=str(meta.get("embedding_model", "")),
            dimensions=int(meta.get("dimensions", 0)),
            chunks_sha256=str(meta.get("chunks_sha256", "")),
            metadata_keys=tuple(meta.get("metadata_keys", [])),
        )


def _default_embed_model() -> BaseEmbedding:
    """Return the best available embedding model, probed once.

    Probes the primary model with a short text. If it fails, falls back
    to the next model. This ensures ALL chunks in one index use the same
    provider + model + dimensions.
    """
    if settings.siliconflow_api_key:
        from app.services.embeddings.siliconflow_embedding import (
            SiliconFlowEmbedding,
        )

        # Try primary model
        primary = SiliconFlowEmbedding(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.embedding_model,
            allow_runtime_fallback=False,
        )
        if _probe_embed_model(primary):
            return primary

        # Try fallback model (BGE)
        if primary._fallback_model and primary._fallback_model != primary.model_name:
            fallback = SiliconFlowEmbedding(
                api_key=settings.siliconflow_api_key,
                base_url=settings.siliconflow_base_url,
                model=primary._fallback_model,
                allow_runtime_fallback=False,
            )
            if _probe_embed_model(fallback):
                return fallback

    return ManualHashEmbedding()


def _probe_embed_model(embedding: BaseEmbedding) -> bool:
    """Test the embedding model with a short text. Returns True if it works."""
    try:
        result = embedding._get_text_embedding("probe: 发动机维修")
        return isinstance(result, list) and len(result) > 0
    except Exception:
        return False


def build_manual_vector_index(
    *,
    chunks_path: Path | None = None,
    index_dir: Path | None = None,
    overwrite: bool = True,
    show_progress: bool = False,
    embed_model: BaseEmbedding | None = None,
) -> ManualVectorIndexBuildResult:
    source_path = chunks_path or settings.data_path / "processed" / "manual_chunks.jsonl"
    target_dir = index_dir or DEFAULT_INDEX_DIR
    documents = load_manual_documents(source_path)
    if not documents:
        raise ValueError(f"No manual documents found in {source_path}")

    embedding = embed_model or _default_embed_model()
    _disable_runtime_fallback_for_build(embedding)
    if overwrite:
        _remove_existing_index_dir(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    index = VectorStoreIndex.from_documents(
        documents,
        embed_model=embedding,
        transformations=[],
        show_progress=show_progress,
    )
    index.set_index_id(MANUAL_INDEX_ID)
    index.storage_context.persist(persist_dir=str(target_dir))

    # Write index fingerprint for load-time verification
    chunks_sha256 = _file_sha256(source_path)
    meta = {
        "chunks_path": str(source_path.resolve()),
        "index_dir": str(target_dir.resolve()),
        "document_count": len(documents),
        "index_id": MANUAL_INDEX_ID,
        "provider": _provider_name(embedding),
        "embedding_model": embedding.model_name,
        "dimensions": getattr(embedding, "dimensions", 0),
        "chunks_sha256": chunks_sha256,
        "metadata_keys": list(REQUIRED_DOCUMENT_METADATA_KEYS),
    }
    (target_dir / "index_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return ManualVectorIndexBuildResult(
        chunks_path=source_path.resolve(),
        index_dir=target_dir.resolve(),
        document_count=len(documents),
        index_id=MANUAL_INDEX_ID,
        provider=meta["provider"],
        embedding_model=embedding.model_name,
        dimensions=meta["dimensions"],
        chunks_sha256=chunks_sha256,
        metadata_keys=REQUIRED_DOCUMENT_METADATA_KEYS,
    )


def load_manual_vector_index(
    *,
    index_dir: Path | None = None,
    embed_model: BaseEmbedding | None = None,
) -> VectorStoreIndex:
    target_dir = index_dir or DEFAULT_INDEX_DIR
    embedding = embed_model or _default_embed_model()

    # Verify index fingerprint against current embedding config
    _verify_index_compat(target_dir, embedding)

    storage_context = StorageContext.from_defaults(persist_dir=str(target_dir))
    return load_index_from_storage(
        storage_context,
        index_id=MANUAL_INDEX_ID,
        embed_model=embedding,
    )


def get_manual_vector_retriever(
    *,
    index_dir: Path | None = None,
    similarity_top_k: int = 5,
    embed_model: BaseEmbedding | None = None,
) -> BaseRetriever:
    index = load_manual_vector_index(index_dir=index_dir, embed_model=embed_model)
    return index.as_retriever(similarity_top_k=similarity_top_k)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and persist the motorcycle manual LlamaIndex vector index."
    )
    parser.add_argument(
        "--chunks-path",
        type=Path,
        default=settings.data_path / "processed" / "manual_chunks.jsonl",
        help="Path to manual_chunks.jsonl.",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=DEFAULT_INDEX_DIR,
        help="Directory where the persisted LlamaIndex index will be written.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not remove an existing index directory before building.",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Show LlamaIndex build progress.",
    )
    args = parser.parse_args(argv)

    result = build_manual_vector_index(
        chunks_path=args.chunks_path,
        index_dir=args.index_dir,
        overwrite=not args.no_overwrite,
        show_progress=args.show_progress,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


def _remove_existing_index_dir(index_dir: Path) -> None:
    resolved_target = index_dir.resolve()
    if not resolved_target.exists():
        return

    resolved_indexes_root = (settings.data_path / "indexes").resolve()
    if not resolved_target.is_relative_to(resolved_indexes_root):
        raise ValueError(
            f"Refusing to clear index directory outside {resolved_indexes_root}: "
            f"{resolved_target}"
        )
    if resolved_target.exists():
        shutil.rmtree(resolved_target)


def _tokenize_for_embedding(text: str) -> list[str]:
    normalized = text.lower()
    tokens: list[str] = []
    for match in TOKEN_RE.findall(normalized):
        token = match.strip()
        if not token:
            continue
        tokens.append(token)
        if _is_cjk(token):
            tokens.extend(
                token[index : index + 2] for index in range(max(0, len(token) - 1))
            )
            tokens.extend(
                token[index : index + 3] for index in range(max(0, len(token) - 2))
            )
    return tokens


def _is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provider_name(embedding: BaseEmbedding) -> str:
    class_name = type(embedding).__name__
    if "SiliconFlow" in class_name:
        return "siliconflow"
    if "ManualHash" in class_name:
        return "local"
    return class_name.lower()


def _verify_index_compat(target_dir: Path, embedding: BaseEmbedding) -> None:
    meta_path = target_dir / "index_meta.json"
    if not meta_path.exists():
        logging.getLogger(__name__).warning(
            "Manual vector index has no index_meta.json; allowing legacy load without "
            "embedding compatibility verification."
        )
        return

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    stored_model = meta.get("embedding_model", "")
    stored_dims = int(meta.get("dimensions", 0) or 0)
    stored_provider = meta.get("provider", "")
    current_model = embedding.model_name
    current_dims = int(getattr(embedding, "dimensions", 0) or 0)
    current_provider = _provider_name(embedding)

    if (
        stored_model != current_model
        or stored_dims != current_dims
        or stored_provider != current_provider
    ):
        raise RuntimeError(
            "Embedding index mismatch: "
            f"stored={stored_provider}/{stored_model}({stored_dims}), "
            f"current={current_provider}/{current_model}({current_dims}). "
            "Please rebuild the manual vector index."
        )


def _disable_runtime_fallback_for_build(embedding: BaseEmbedding) -> None:
    if hasattr(embedding, "_allow_runtime_fallback"):
        setattr(embedding, "_allow_runtime_fallback", False)


if __name__ == "__main__":
    raise SystemExit(main())
