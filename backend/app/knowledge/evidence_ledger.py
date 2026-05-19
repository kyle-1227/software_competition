from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.knowledge.base import PsycopgRepository, jsonb
from app.schemas.query import EvidenceItem
from app.services.tracing.serializers import sanitize_trace_dict


class EvidenceLedgerRepository(PsycopgRepository):
    def record_evidence(
        self,
        evidence: list[EvidenceItem | dict[str, Any]],
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        runtime_request_id: str | None = None,
        document_version_id: str | None = None,
        retrieval_method: str | None = None,
    ) -> list[str]:
        rows = [
            self.evidence_row(
                item,
                trace_id=trace_id,
                run_id=run_id,
                runtime_request_id=runtime_request_id,
                document_version_id=document_version_id,
                retrieval_method=retrieval_method,
            )
            for item in evidence
        ]
        if not rows:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO evidence_ledger (
                            evidence_id, trace_id, run_id, runtime_request_id,
                            document_id, document_version_id, chunk_id, source,
                            source_id, page, snippet, score, retrieval_method,
                            is_placeholder, metadata_json
                        )
                        VALUES (
                            %(evidence_id)s, %(trace_id)s, %(run_id)s,
                            %(runtime_request_id)s, %(document_id)s,
                            %(document_version_id)s, %(chunk_id)s, %(source)s,
                            %(source_id)s, %(page)s, %(snippet)s, %(score)s,
                            %(retrieval_method)s, %(is_placeholder)s,
                            %(metadata_json)s
                        )
                        """,
                        {**row, "metadata_json": jsonb(row["metadata_json"])},
                    )
        return [row["evidence_id"] for row in rows]

    @staticmethod
    def evidence_row(
        item: EvidenceItem | dict[str, Any],
        *,
        trace_id: str | None,
        run_id: str | None,
        runtime_request_id: str | None,
        document_version_id: str | None = None,
        retrieval_method: str | None,
    ) -> dict[str, Any]:
        data = item.model_dump(mode="json") if isinstance(item, EvidenceItem) else dict(item)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        retriever = str(metadata.get("retriever") or "")
        is_placeholder = retriever in {
            "llama-index-placeholder",
            "manual_lookup-degraded",
        } or "placeholder" in str(data.get("source") or "").lower()
        return {
            "evidence_id": str(uuid4()),
            "trace_id": trace_id,
            "run_id": run_id,
            "runtime_request_id": runtime_request_id,
            "document_id": metadata.get("document_id") or metadata.get("manual_id"),
            "document_version_id": document_version_id
            or metadata.get("document_version_id"),
            "chunk_id": metadata.get("chunk_id"),
            "source": str(data.get("source") or "unknown"),
            "source_id": metadata.get("source_id")
            or metadata.get("chunk_id")
            or metadata.get("asset_id")
            or data.get("source"),
            "page": data.get("page"),
            "snippet": str(data.get("snippet") or ""),
            "score": data.get("score"),
            "retrieval_method": retrieval_method or retriever or None,
            "is_placeholder": is_placeholder,
            "metadata_json": sanitize_trace_dict(metadata),
        }
