from __future__ import annotations

from app.retrieval.keyword_retriever import KeywordRetriever
from app.retrieval.pgvector_retriever import PgVectorRetriever
from app.retrieval.reranker import RetrievalReranker
from app.retrieval.schemas import RetrievalQuery, RetrievalResult


class HybridRetriever:
    def __init__(
        self,
        *,
        keyword_retriever: KeywordRetriever | None = None,
        pgvector_retriever: PgVectorRetriever | None = None,
        reranker: RetrievalReranker | None = None,
    ) -> None:
        self.keyword_retriever = keyword_retriever or KeywordRetriever()
        self.pgvector_retriever = pgvector_retriever or PgVectorRetriever()
        self.reranker = reranker or RetrievalReranker()

    async def retrieve(self, query: RetrievalQuery | str) -> list[RetrievalResult]:
        request = query if isinstance(query, RetrievalQuery) else RetrievalQuery(query=query)
        results = []
        results.extend(await self.keyword_retriever.retrieve(request))
        results.extend(await self.pgvector_retriever.retrieve(request))
        deduped = _dedupe(results)
        return self.reranker.rerank(request.query, deduped)[: request.top_k]


def _dedupe(results: list[RetrievalResult]) -> list[RetrievalResult]:
    seen: set[str] = set()
    deduped: list[RetrievalResult] = []
    for item in results:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        deduped.append(item)
    return deduped
