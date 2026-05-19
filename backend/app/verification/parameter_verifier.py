from __future__ import annotations

import re
from typing import Any

from app.verification.evidence_verifier import VerificationResult

_PARAMETER_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?(?:\s*(?:-|~|～|至)\s*\d+(?:\.\d+)?)?\s*(?:mm|cm|m|kpa|mpa|n\.?m|v|a|°c|℃)\b",
    re.IGNORECASE,
)


class ParameterVerifier:
    def verify(self, state: dict[str, Any]) -> VerificationResult:
        answer = str(state.get("answer") or "")
        parameters = _PARAMETER_PATTERN.findall(answer)
        if not parameters:
            return VerificationResult(passed=True)
        evidence_text = " ".join(
            str(item.get("snippet") or "")
            for item in state.get("evidence", [])
            if isinstance(item, dict)
        ).lower()
        missing = [
            parameter
            for parameter in parameters
            if parameter.lower().replace(" ", "") not in evidence_text.replace(" ", "")
        ]
        if missing:
            return VerificationResult(
                passed=False,
                issues=[f"answer contains parameter not present in evidence: {value}" for value in missing],
            )
        return VerificationResult(passed=True)
