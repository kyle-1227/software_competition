from __future__ import annotations

from app.retrieval.schemas import RetrievalResult


class RetrievalReranker:
    def rerank(self, query: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        del query
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)
