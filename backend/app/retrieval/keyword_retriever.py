from __future__ import annotations

import re
from typing import Any

from app.db.session import database_url
from app.retrieval.schemas import RetrievalQuery, RetrievalResult


class KeywordRetriever:
    """PostgreSQL evidence_ledger keyword retriever.

    First version uses ILIKE over snippet/source/source_id. If DATABASE_URL is
    not configured, it returns an empty list so the API remains offline-safe.
    """

    def __init__(self, database_url_value: str | None = None) -> None:
        self.database_url = database_url_value or database_url()

    async def retrieve(self, query: RetrievalQuery | str) -> list[RetrievalResult]:
        request = query if isinstance(query, RetrievalQuery) else RetrievalQuery(query=query)
        if not self.database_url or not request.query.strip():
            return []
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - dependency guard.
            raise RuntimeError("psycopg is required for PostgreSQL retrieval") from exc

        terms = _query_terms(request.query)
        if not terms:
            return []
        pattern = "%" + "%".join(terms) + "%"
        with psycopg.connect(str(self.database_url), row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT evidence_id, source, source_id, page, snippet, score,
                           retrieval_method, metadata_json
                    FROM evidence_ledger
                    WHERE is_placeholder = false
                      AND (
                        snippet ILIKE %(pattern)s
                        OR source ILIKE %(pattern)s
                        OR COALESCE(source_id, '') ILIKE %(pattern)s
                      )
                    ORDER BY COALESCE(score, 0) DESC, created_at DESC
                    LIMIT %(limit)s
                    """,
                    {"pattern": pattern, "limit": request.top_k},
                )
                rows = cur.fetchall()
        return [
            RetrievalResult(
                evidence_id=str(row["evidence_id"]),
                source=str(row["source"]),
                source_id=row.get("source_id"),
                page=row.get("page"),
                snippet=str(row["snippet"]),
                score=row.get("score"),
                retrieval_method=str(row.get("retrieval_method") or "keyword"),
                metadata=row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {},
            )
            for row in rows
        ]


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query)
    return [term for term in terms[:8] if term.strip()]
