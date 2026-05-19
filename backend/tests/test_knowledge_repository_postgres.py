from __future__ import annotations

import os

import pytest

from app.schemas.query import EvidenceItem
from app.knowledge.evidence_ledger import EvidenceLedgerRepository
from app.knowledge.repository import KnowledgeRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "true"
    or not (
        os.getenv("KNOWLEDGE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("TRACE_DATABASE_URL")
    ),
    reason=(
        "PostgreSQL knowledge repository tests require RUN_POSTGRES_TESTS=true "
        "and KNOWLEDGE_DATABASE_URL, DATABASE_URL, or TRACE_DATABASE_URL"
    ),
)


def test_postgres_knowledge_source_is_ready() -> None:
    database_url = (
        os.environ.get("KNOWLEDGE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ["TRACE_DATABASE_URL"]
    )
    repository = KnowledgeRepository(database_url)

    repository.initialize(include_trace_schema=True)

    with repository.documents._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            assert cur.fetchone()[0] == "vector"

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = ANY(%s)
                """,
                (["documents", "chunks", "evidence_ledger", "embeddings"],),
            )
            tables = {row[0] for row in cur.fetchall()}
            assert tables == {
                "documents",
                "chunks",
                "evidence_ledger",
                "embeddings",
            }


def test_postgres_evidence_ledger_repository_writes_evidence() -> None:
    database_url = (
        os.environ.get("KNOWLEDGE_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ["TRACE_DATABASE_URL"]
    )
    repository = KnowledgeRepository(database_url)
    repository.initialize(include_trace_schema=True)

    ledger = EvidenceLedgerRepository(database_url)
    evidence_ids = ledger.record_evidence(
        [
            EvidenceItem(
                source="manual.pdf",
                page=3,
                snippet="spark plug inspection",
                score=0.91,
                metadata={"chunk_id": None, "retriever": "integration-test"},
            )
        ],
        runtime_request_id="postgres-knowledge-test",
        retrieval_method="integration-test",
    )

    assert len(evidence_ids) == 1
    with repository.documents._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, source_id, page, snippet, score, retrieval_method, is_placeholder
                FROM evidence_ledger
                WHERE evidence_id = %s
                """,
                (evidence_ids[0],),
            )
            row = cur.fetchone()

    assert row[0] == "manual.pdf"
    assert row[1] == "manual.pdf"
    assert row[2] == 3
    assert row[3] == "spark plug inspection"
    assert float(row[4]) == pytest.approx(0.91)
    assert row[5] == "integration-test"
    assert row[6] is False
