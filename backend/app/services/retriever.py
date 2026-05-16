from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.query import EvidenceItem
from app.schemas.trace import SpanKind
from app.services.manual_vector_indexer import (
    DEFAULT_INDEX_DIR,
    get_manual_vector_retriever,
    _tokenize_for_embedding,
)
from app.services.tracing.context import trace_span
from app.services.tracing.helpers import summarize_retrieval_result

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
        trace_store: Any | None = None,
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
        self.trace_store = trace_store

    async def search_evidence(
        self,
        question: str,
        device_model: str | None = None,
        *,
        trace_store: Any = None,
        trace_id: str | None = None,
    ) -> list[EvidenceItem]:
        active_trace_store = trace_store or self.trace_store
        # Query rewriting (HyDE): generate hypothetical document for better retrieval
        query = question
        fallback_used = False
        async with trace_span(
            active_trace_store,
            trace_id,
            "retriever.query_rewrite",
            SpanKind.RETRIEVER,
            inputs={
                "query_preview": _preview(question),
                "query_length": len(question),
            },
            metadata={
                "hyde_enabled": self._query_rewriter is not None,
                "rewriter_model": _component_name(self._query_rewriter),
                "query_length": len(question),
            },
        ) as span:
            if self._query_rewriter:
                try:
                    query = await self._query_rewriter.rewrite(question)
                    fallback_used = query == question
                except Exception:
                    fallback_used = True
                    query = question
            span.set_metadata(
                {
                    "hyde_enabled": self._query_rewriter is not None,
                    "rewriter_model": _component_name(self._query_rewriter),
                    "fallback_used": fallback_used,
                    "query_length": len(question),
                    "rewritten_query_length": len(query),
                }
            )
            span.set_outputs(
                {
                    "fallback_used": fallback_used,
                    "rewritten_query_length": len(query),
                }
            )

        vector_meta = {
            "top_k": self.top_k,
            **_index_trace_meta(self.index_path),
        }
        async with trace_span(
            active_trace_store,
            trace_id,
            "retriever.vector_search",
            SpanKind.RETRIEVER,
            inputs={
                "query_preview": _preview(query),
                "query_length": len(query),
                "device_model": device_model,
            },
            metadata=vector_meta,
        ) as span:
            retrieval_fallback_used = False
            try:
                nodes = await self._retrieve_nodes(
                    query,
                    trace_store=active_trace_store,
                    trace_id=trace_id,
                )
            except Exception as exc:
                if "Embedding index mismatch" in str(exc) and not _is_default_index_path(
                    self.index_path
                ):
                    raise
                retrieval_fallback_used = True
                fallback = self._keyword_fallback(question)
                evidence = fallback or [
                    _placeholder_evidence(
                        question=question,
                        device_model=device_model,
                    )
                ]
            else:
                if not nodes:
                    retrieval_fallback_used = True
                    fallback = self._keyword_fallback(question)
                    evidence = fallback or [
                        _placeholder_evidence(
                            question=question,
                            device_model=device_model,
                        )
                    ]
                else:
                    evidence = [_node_to_evidence(node) for node in nodes]
                    evidence = [item for item in evidence if item.snippet]
                    if not evidence:
                        retrieval_fallback_used = True
                        fallback = self._keyword_fallback(question)
                        evidence = fallback or [
                            _placeholder_evidence(
                                question=question,
                                device_model=device_model,
                            )
                        ]
            evidence_dicts = [item.model_dump(mode="json") for item in evidence]
            summary = summarize_retrieval_result(evidence_dicts)
            span.set_metadata(
                {
                    **vector_meta,
                    **summary,
                    "fallback_used": retrieval_fallback_used,
                }
            )
            span.set_outputs(summary)
            return evidence

    async def search(
        self,
        question: str,
        device_model: str | None = None,
        *,
        trace_store: Any = None,
        trace_id: str | None = None,
    ) -> list[EvidenceItem]:
        return await self.search_evidence(
            question,
            device_model,
            trace_store=trace_store,
            trace_id=trace_id,
        )

    async def _retrieve_nodes(
        self,
        question: str,
        *,
        trace_store: Any = None,
        trace_id: str | None = None,
    ) -> list[Any]:
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
            async with trace_span(
                trace_store,
                trace_id,
                "reranker.score",
                SpanKind.RERANKER,
                inputs={
                    "query_preview": _preview(question),
                    "candidate_count": len(docs),
                },
                metadata={
                    "reranker_enabled": True,
                    "reranker_model": getattr(self._reranker, "model", None),
                    "candidate_count": len(docs),
                    "top_n": getattr(self._reranker, "top_n", self.top_k),
                },
            ) as span:
                ranked = self._reranker.rerank(question, docs)
                fallback_used = _identity_ranking_used(ranked, len(docs))
                ranked.sort(key=lambda x: x[1], reverse=True)
                nodes = [nodes[i] for i, _ in ranked[:self.top_k]]
                span.set_metadata(
                    {
                        "reranker_enabled": True,
                        "reranker_model": getattr(self._reranker, "model", None),
                        "candidate_count": len(docs),
                        "top_n": getattr(self._reranker, "top_n", self.top_k),
                        "degraded": fallback_used,
                        "fallback_used": fallback_used,
                    }
                )
                span.set_outputs(
                    {
                        "candidate_count": len(docs),
                        "selected_count": len(nodes),
                        "fallback_used": fallback_used,
                    }
                )

        return nodes

    def _keyword_fallback(self, question: str) -> list[EvidenceItem]:
        if not self._use_default_keyword_fallback:
            return []
        return _default_keyword_fallback(self.index_path, question, self.top_k)


def _preview(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _component_name(component: Any) -> str | None:
    if component is None:
        return None
    return getattr(component, "model", None) or component.__class__.__name__


def _index_trace_meta(index_path: Path) -> dict[str, Any]:
    meta_path = index_path / "index_meta.json"
    meta: dict[str, Any] = {
        "embedding_provider": None,
        "embedding_model": None,
        "index_meta_loaded": False,
    }
    try:
        if not meta_path.exists():
            return meta
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return meta
    meta["index_meta_loaded"] = True
    meta["embedding_provider"] = stored.get("provider")
    meta["embedding_model"] = stored.get("embedding_model")
    return meta


def _identity_ranking_used(ranked: list[tuple[int, float]], candidate_count: int) -> bool:
    if len(ranked) != candidate_count:
        return False
    return all(index == position and score == 0.0 for position, (index, score) in enumerate(ranked))


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
