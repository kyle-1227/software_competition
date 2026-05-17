from __future__ import annotations

from evals.graders.base import FailureTypeGrader, assertion_exists, check


class EvaluatorLowConfidenceGrader(FailureTypeGrader):
    failure_type = "evaluator_low_confidence"
    name = "evaluator_low_confidence"

    def _specific_checks(self, case, reasons, checked) -> None:
        confidence = case.get("confidence")
        low = confidence is None
        if confidence is not None:
            try:
                low = float(confidence) < 0.7
            except (TypeError, ValueError):
                low = False
        check(
            low,
            "confidence_below_threshold",
            True,
            "evaluator low confidence case must have confidence below threshold or unknown",
            reasons,
            checked,
        )
        check(
            assertion_exists(case, "confidence_below_threshold"),
            "confidence_below_threshold_assertion",
            True,
            "evaluator low confidence case must assert threshold failure",
            reasons,
            checked,
        )
