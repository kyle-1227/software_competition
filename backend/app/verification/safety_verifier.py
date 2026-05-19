from __future__ import annotations

from typing import Any

from app.verification.evidence_verifier import VerificationResult


class SafetyVerifier:
    unsafe_terms = ("带电操作", "跳过防护", "直接短接", "disable safety")

    def verify(self, state: dict[str, Any]) -> VerificationResult:
        answer = str(state.get("answer") or "").lower()
        issues = [f"unsafe instruction detected: {term}" for term in self.unsafe_terms if term.lower() in answer]
        return VerificationResult(passed=not issues, issues=issues)
