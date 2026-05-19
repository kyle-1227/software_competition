from __future__ import annotations

from typing import Any

from app.verification.evidence_verifier import VerificationResult


class SOPVerifier:
    def verify(self, state: dict[str, Any]) -> VerificationResult:
        del state
        return VerificationResult(passed=True)
