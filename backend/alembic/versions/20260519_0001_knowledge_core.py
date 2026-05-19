"""knowledge core schema

Revision ID: 20260519_0001
Revises:
Create Date: 2026-05-19
"""

from __future__ import annotations

from alembic import op

from app.services.knowledge.migrations import KNOWLEDGE_MIGRATIONS
from app.services.tracing.migrations import TRACE_MIGRATIONS

revision = "20260519_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for migration in (*TRACE_MIGRATIONS, *KNOWLEDGE_MIGRATIONS):
        for statement in migration.statements:
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
    op.drop_table("evidence_ledger")
    op.drop_table("embeddings")
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("assets")
    op.drop_table("documents")
