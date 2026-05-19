from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)


class EvidenceVerifier:
    def verify(self, state: dict[str, Any]) -> VerificationResult:
        evidence = [item for item in state.get("evidence", []) if isinstance(item, dict)]
        if not evidence:
            return VerificationResult(passed=False, issues=["missing evidence"])
        verified = [item for item in evidence if _has_evidence_id(item) and not _is_placeholder(item)]
        if not verified:
            return VerificationResult(
                passed=False,
                issues=["missing evidence_id for retrieved evidence"],
            )
        return VerificationResult(passed=True)


def _has_evidence_id(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return bool(item.get("evidence_id") or metadata.get("evidence_id"))


def _is_placeholder(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    retriever = str(metadata.get("retriever") or "").lower()
    source = str(item.get("source") or "").lower()
    return (
        bool(item.get("is_placeholder"))
        or "placeholder" in retriever
        or "placeholder" in source
        or retriever == "manual_lookup-degraded"
    )
