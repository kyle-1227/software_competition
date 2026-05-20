from __future__ import annotations

from typing import Any

from app.verification.evidence_verifier import EvidenceVerifier, VerificationResult
from app.verification.parameter_verifier import ParameterVerifier
from app.verification.safety_verifier import SafetyVerifier
from app.verification.sop_verifier import SOPVerifier

VERIFICATION_FAILED_ANSWER = "当前证据不足，不能给出确定维修结论。建议补充手册资料或设备型号后再诊断。"


class DiagnosticFinalVerifier:
    """Strong verifier for diagnostic answers that claim maintenance guidance."""

    def __init__(
        self,
        *,
        evidence_verifier: EvidenceVerifier | None = None,
        parameter_verifier: ParameterVerifier | None = None,
        sop_verifier: SOPVerifier | None = None,
        safety_verifier: SafetyVerifier | None = None,
    ) -> None:
        self.evidence_verifier = evidence_verifier or EvidenceVerifier()
        self.parameter_verifier = parameter_verifier or ParameterVerifier()
        self.sop_verifier = sop_verifier or SOPVerifier()
        self.safety_verifier = safety_verifier or SafetyVerifier()

    def verify(self, state: dict[str, Any]) -> VerificationResult:
        issues: list[str] = []
        for verifier in (
            self.evidence_verifier,
            self.parameter_verifier,
            self.sop_verifier,
            self.safety_verifier,
        ):
            result = verifier.verify(state)
            if not result.passed:
                issues.extend(result.issues)
        return VerificationResult(passed=not issues, issues=issues)

    def failure_update(self, issues: list[str]) -> dict[str, Any]:
        return {
            "answer": VERIFICATION_FAILED_ANSWER,
            "evaluation": {
                "is_safe": True,
                "is_compliant": False,
                "confidence": 0.2,
                "issues": issues,
                "feedback": "verification_failed",
            },
            "fail_safe_reason": "verification_failed",
            "status": "completed",
            "verification_passed": False,
            "verification_issues": issues,
        }


class TerminalStateVerifier:
    """Verifier for terminal non-diagnostic states.

    Pending approval, clarification, and fail-safe messages are not diagnostic
    answers. They may skip diagnostic verification only when the skip reason is
    explicit and traceable.
    """

    TERMINAL_FIELDS = (
        ("pending_approval", "pending_approval"),
        ("clarification_question", "clarification"),
        ("fail_safe_reason", "fail_safe"),
    )

    def terminal_skip_update(self, state: dict[str, Any]) -> dict[str, Any] | None:
        for field, reason in self.TERMINAL_FIELDS:
            if field == "pending_approval" and state.get("status") != "pending_approval":
                continue
            if field != "pending_approval" and not state.get(field):
                continue
            return {
                "verification_passed": True,
                "verification_issues": [],
                "verification_skipped_reason": reason,
            }
        return None


class FinalVerifier(DiagnosticFinalVerifier):
    """Backward-compatible alias for older imports."""
