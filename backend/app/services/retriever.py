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

DEFAULT_MANUAL_SOURCE = "manual"


class Retriever:
    """Legacy vector retriever.

    This adapter no longer fabricates placeholder evidence. If retrieval fails
    or returns no results, callers receive an empty list and the final verifier
    is responsible for blocking deterministic maintenance conclusions.
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
        self.chunks_path = chunks_path
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
        del device_model
        active_trace_store = trace_store or self.trace_store
        query = question
        async with trace_span(
            active_trace_store,
            trace_id,
            "retriever.query_rewrite",
            SpanKind.RETRIEVER,
            inputs={"query_preview": _preview(question), "query_length": len(question)},
            metadata={"hyde_enabled": self._query_rewriter is not None},
        ) as span:
            fallback_used = False
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
                    "fallback_used": fallback_used,
                    "query_length": len(question),
                    "rewritten_query_length": len(query),
                }
            )
            span.set_outputs({"fallback_used": fallback_used})

        vector_meta = {"top_k": self.top_k, **_index_trace_meta(self.index_path)}
        async with trace_span(
            active_trace_store,
            trace_id,
            "retriever.vector_search",
            SpanKind.RETRIEVER,
            inputs={"query_preview": _preview(query), "query_length": len(query)},
            metadata=vector_meta,
        ) as span:
            retrieval_fallback_used = False
            try:
                nodes = await self._retrieve_nodes(
                    query,
                    trace_store=active_trace_store,
                    trace_id=trace_id,
                )
                evidence = [_node_to_evidence(node) for node in nodes]
                evidence = [item for item in evidence if item.snippet]
            except Exception as exc:
                if "Embedding index mismatch" in str(exc) and not _is_default_index_path(
                    self.index_path
                ):
                    raise
                retrieval_fallback_used = True
                evidence = self._keyword_fallback(question)
            if not evidence:
                retrieval_fallback_used = True
                evidence = self._keyword_fallback(question)

            summary = summarize_retrieval_result(
                [item.model_dump(mode="json") for item in evidence]
            )
            span.set_metadata(
                {**vector_meta, **summary, "fallback_used": retrieval_fallback_used}
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
        if self._reranker and len(nodes) > self.top_k:
            docs = [_node_text(getattr(node, "node", node)) for node in nodes]
            async with trace_span(
                trace_store,
                trace_id,
                "reranker.score",
                SpanKind.RERANKER,
                inputs={"query_preview": _preview(question), "candidate_count": len(docs)},
                metadata={"reranker_enabled": True, "candidate_count": len(docs)},
            ) as span:
                ranked = self._reranker.rerank(question, docs)
                ranked.sort(key=lambda item: item[1], reverse=True)
                nodes = [nodes[index] for index, _ in ranked[: self.top_k]]
                span.set_outputs({"candidate_count": len(docs), "selected_count": len(nodes)})
        return nodes

    def _keyword_fallback(self, question: str) -> list[EvidenceItem]:
        if not self._use_default_keyword_fallback:
            return []
        return _default_keyword_fallback(self.chunks_path, self.index_path, question, self.top_k)


def _preview(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


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
    return metadata if isinstance(metadata, dict) else {}


def _node_text(node: Any) -> str:
    if hasattr(node, "get_content"):
        content = node.get_content(metadata_mode="none")
        if content:
            return str(content)
    return str(getattr(node, "text", "") or "")


def _snippet(text: str, limit: int = 220) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _default_keyword_fallback(
    chunks_path: Path | None,
    index_path: Path,
    question: str,
    top_k: int,
) -> list[EvidenceItem]:
    if chunks_path is None and not _is_default_index_path(index_path):
        return []
    resolved_chunks_path = chunks_path or settings.data_path / "processed" / "manual_chunks.jsonl"
    if not resolved_chunks_path.exists():
        return []
    query_tokens = set(_tokenize_for_embedding(question))
    if not query_tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for line in resolved_chunks_path.read_text(encoding="utf-8").splitlines():
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
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_chunk_to_evidence(chunk, score) for score, chunk in scored[:top_k]]


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
