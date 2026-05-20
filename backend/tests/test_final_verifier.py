from __future__ import annotations

from app.verification.final_verifier import (
    DiagnosticFinalVerifier,
    FinalVerifier,
    TerminalStateVerifier,
    VERIFICATION_FAILED_ANSWER,
)


def test_final_verifier_blocks_missing_evidence_id() -> None:
    verifier = FinalVerifier()

    result = verifier.verify(
        {
            "answer": "Replace the part.",
            "evidence": [{"source": "manual", "snippet": "Check connector."}],
        }
    )

    assert result.passed is False
    assert "missing evidence_id" in " ".join(result.issues)


def test_final_verifier_blocks_parameter_not_in_evidence() -> None:
    verifier = FinalVerifier()

    result = verifier.verify(
        {
            "answer": "Set clearance to 0.7 mm.",
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "source": "manual",
                    "snippet": "Check the connector.",
                    "metadata": {},
                }
            ],
        }
    )

    assert result.passed is False
    assert "0.7 mm" in " ".join(result.issues)


def test_final_verifier_failure_update_shape() -> None:
    update = FinalVerifier().failure_update(["missing evidence"])

    assert update["answer"] == VERIFICATION_FAILED_ANSWER
    assert update["evaluation"]["is_safe"] is True
    assert update["evaluation"]["is_compliant"] is False
    assert update["evaluation"]["confidence"] == 0.2
    assert update["fail_safe_reason"] == "verification_failed"


def test_terminal_state_verifier_records_pending_approval_skip_reason() -> None:
    update = TerminalStateVerifier().terminal_skip_update(
        {"status": "pending_approval", "answer": "waiting"}
    )

    assert update is not None
    assert update["verification_skipped_reason"] == "pending_approval"


def test_approved_diagnostic_answer_uses_strong_verifier() -> None:
    result = DiagnosticFinalVerifier().verify(
        {
            "approval_decision": "approved",
            "approved_approval_id": "approval-1",
            "answer": "Set clearance to 0.7 mm.",
            "evidence": [
                {
                    "evidence_id": "ev-1",
                    "source": "manual",
                    "snippet": "Check the connector.",
                    "metadata": {},
                }
            ],
        }
    )

    assert result.passed is False
    assert "0.7 mm" in " ".join(result.issues)
