from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from app.schemas.query import EvidenceItem
from app.services.manual_vector_indexer import (
    DEFAULT_INDEX_DIR,
    get_manual_vector_retriever,
)


DEFAULT_MANUAL_SOURCE = "维修手册_41页_分块整理.xlsx"


class Retriever:
    """Retriever backed only by the persisted LlamaIndex manual vector index."""

    def __init__(
        self,
        chunks_path: Path | None = None,
        index_path: Path | None = None,
        top_k: int = 5,
        vector_retriever: Any | None = None,
    ) -> None:
        # chunks_path is kept only for older call sites/tests. Retrieval no longer
        # reads JSONL directly; build the vector index first instead.
        del chunks_path
        self.index_path = index_path or DEFAULT_INDEX_DIR
        self.top_k = top_k
        self._vector_retriever = vector_retriever

    async def search_evidence(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        try:
            nodes = self._retrieve_nodes(question)
        except Exception:
            return [_placeholder_evidence(question=question, device_model=device_model)]

        if not nodes:
            return [_placeholder_evidence(question=question, device_model=device_model)]

        evidence = [_node_to_evidence(node) for node in nodes]
        evidence = [item for item in evidence if item.snippet]
        if not evidence:
            return [_placeholder_evidence(question=question, device_model=device_model)]
        return evidence

    async def search(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        return await self.search_evidence(question, device_model)

    def _retrieve_nodes(self, question: str) -> list[Any]:
        if self._vector_retriever is None:
            self._vector_retriever = get_manual_vector_retriever(
                index_dir=self.index_path,
                similarity_top_k=self.top_k,
            )
        return list(self._vector_retriever.retrieve(question))


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
