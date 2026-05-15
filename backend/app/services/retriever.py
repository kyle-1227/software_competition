from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.query import EvidenceItem
from app.services.manual_vector_indexer import (
    DEFAULT_INDEX_DIR,
    get_manual_vector_retriever,
    _tokenize_for_embedding,
)

DEFAULT_MANUAL_SOURCE = "维修手册_41页_分块整理.xlsx"


class Retriever:
    """Retriever backed by the persisted LlamaIndex manual vector index,
    with optional Reranker and QueryRewriter (HyDE) support.
    """

    def __init__(
        self,
        chunks_path: Path | None = None,
        index_path: Path | None = None,
        top_k: int = 5,
        vector_retriever: Any | None = None,
        reranker: Any | None = None,
        query_rewriter: Any | None = None,
        retrieve_multiplier: int = 4,
        embed_model: Any | None = None,
    ) -> None:
        del chunks_path
        self.index_path = index_path or DEFAULT_INDEX_DIR
        self.top_k = top_k
        self._vector_retriever = vector_retriever
        self._use_default_keyword_fallback = vector_retriever is None
        self._reranker = reranker
        self._query_rewriter = query_rewriter
        self._retrieve_multiplier = retrieve_multiplier
        self._embed_model = embed_model

    async def search_evidence(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        # Query rewriting (HyDE): generate hypothetical document for better retrieval
        query = question
        if self._query_rewriter:
            try:
                query = await self._query_rewriter.rewrite(question)
            except Exception:
                pass

        try:
            nodes = self._retrieve_nodes(query)
        except Exception as exc:
            if "Embedding index mismatch" in str(exc) and not _is_default_index_path(
                self.index_path
            ):
                raise
            fallback = self._keyword_fallback(question)
            return fallback or [_placeholder_evidence(question=question, device_model=device_model)]

        if not nodes:
            fallback = self._keyword_fallback(question)
            return fallback or [_placeholder_evidence(question=question, device_model=device_model)]

        evidence = [_node_to_evidence(node) for node in nodes]
        evidence = [item for item in evidence if item.snippet]
        if not evidence:
            fallback = self._keyword_fallback(question)
            return fallback or [_placeholder_evidence(question=question, device_model=device_model)]
        return evidence

    async def search(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        return await self.search_evidence(question, device_model)

    def _retrieve_nodes(self, question: str) -> list[Any]:
        retrieve_k = self.top_k * self._retrieve_multiplier if self._reranker else self.top_k
        if self._vector_retriever is None:
            self._vector_retriever = get_manual_vector_retriever(
                index_dir=self.index_path,
                similarity_top_k=retrieve_k,
                embed_model=self._embed_model,
            )
        nodes = list(self._vector_retriever.retrieve(question))

        # Reranker: re-rank candidates by relevance, keep top_k
        if self._reranker and len(nodes) > self.top_k:
            docs = [_node_text(getattr(n, "node", n)) for n in nodes]
            ranked = self._reranker.rerank(question, docs)
            ranked.sort(key=lambda x: x[1], reverse=True)
            nodes = [nodes[i] for i, _ in ranked[:self.top_k]]

        return nodes

    def _keyword_fallback(self, question: str) -> list[EvidenceItem]:
        if not self._use_default_keyword_fallback:
            return []
        return _default_keyword_fallback(self.index_path, question, self.top_k)


def _node_to_evidence(node_with_score: Any) -> EvidenceItem:
    node = getattr(node_with_score, "node", node_with_score)
    metadata = _node_metadata(node)
    text = _node_text(node)

    return EvidenceItem(
        source=str(metadata.get("source") or DEFAULT_MANUAL_SOURCE),
        page=_optional_int(metadata.get("page")),
        snippet=_snippet(text),
        score=_optional_float(getattr(node_with_score, "score", None)),
        metadata={
            "chapter": metadata.get("chapter"),
            "section": metadata.get("section"),
            "block_type": metadata.get("block_type"),
            "chunk_id": metadata.get("chunk_id"),
        },
    )


def _node_metadata(node: Any) -> dict[str, Any]:
    metadata = getattr(node, "metadata", None)
    if isinstance(metadata, dict):
        return metadata
    return {}


def _node_text(node: Any) -> str:
    if hasattr(node, "get_content"):
        content = node.get_content(metadata_mode="none")
        if content:
            return str(content)
    return str(getattr(node, "text", "") or "")


def _snippet(text: str, limit: int = 220) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _placeholder_evidence(question: str, device_model: str | None) -> EvidenceItem:
    device_hint = device_model or "未知型号"
    return EvidenceItem(
        source=f"manual::{device_hint}",
        page=None,
        snippet=(
            "当前为 LlamaIndex 兼容占位证据；未能加载或查询持久化手册向量索引，"
            "请先构建 data/indexes/manuals/motorcycle_engine/。"
        ),
        score=0.42,
        metadata={
            "retriever": "llama-index-placeholder",
            "question": question,
        },
    )


def _default_keyword_fallback(
    index_path: Path,
    question: str,
    top_k: int,
) -> list[EvidenceItem]:
    if not _is_default_index_path(index_path):
        return []

    chunks_path = settings.data_path / "processed" / "manual_chunks.jsonl"
    if not chunks_path.exists():
        return []

    query_tokens = set(_tokenize_for_embedding(question))
    if not query_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        haystack = _chunk_haystack(chunk)
        doc_tokens = set(_tokenize_for_embedding(haystack))
        overlap = query_tokens.intersection(doc_tokens)
        if not overlap:
            continue
        score = len(overlap) / max(len(query_tokens), 1)
        if str(chunk.get("block_type", "")).endswith("标准"):
            score += 0.15
        if chunk.get("page") in (3, 15) and any(
            token in haystack for token in ("火花塞", "间隙", "压缩压力", "气门间隙")
        ):
            score += 0.1
        scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        _chunk_to_evidence(chunk, score)
        for score, chunk in scored[:top_k]
    ]


def _is_default_index_path(index_path: Path) -> bool:
    try:
        return index_path.resolve() == DEFAULT_INDEX_DIR.resolve()
    except OSError:
        return False


def _chunk_haystack(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    keywords = chunk.get("keywords") if isinstance(chunk.get("keywords"), list) else []
    return " ".join(
        str(value)
        for value in (
            chunk.get("chapter"),
            chunk.get("section"),
            chunk.get("block_type"),
            " ".join(str(keyword) for keyword in keywords),
            metadata.get("chapter"),
            metadata.get("section"),
            metadata.get("block_type"),
            chunk.get("text"),
        )
        if value
    )


def _chunk_to_evidence(chunk: dict[str, Any], score: float) -> EvidenceItem:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return EvidenceItem(
        source=str(chunk.get("source") or DEFAULT_MANUAL_SOURCE),
        page=_optional_int(chunk.get("page")),
        snippet=_snippet(str(chunk.get("text") or "")),
        score=round(score, 4),
        metadata={
            "chapter": chunk.get("chapter") or metadata.get("chapter"),
            "section": chunk.get("section") or metadata.get("section"),
            "block_type": chunk.get("block_type") or metadata.get("block_type"),
            "chunk_id": chunk.get("chunk_id") or metadata.get("chunk_id"),
        },
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return round(score, 4)
