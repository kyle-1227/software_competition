from __future__ import annotations

from app.retrieval.schemas import RetrievalQuery, RetrievalResult


class PgVectorRetriever:
    """Placeholder facade for pgvector-backed retrieval."""

    async def retrieve(self, query: RetrievalQuery | str) -> list[RetrievalResult]:
        del query
        return []
