from __future__ import annotations

from evals.graders.harness_base import HarnessGradeResult, require, score_from_reasons


class GroundednessGrader:
    name = "groundedness"

    def grade(self, case: dict, observed: dict | None = None) -> HarnessGradeResult:
        observed = observed or case.get("observed") or {}
        reasons: list[str] = []
        answer = str(observed.get("answer") or case.get("answer") or "")
        evidence = observed.get("evidence") or case.get("evidence") or []
        require(bool(case.get("id")), "id is required", reasons)
        require(bool(answer), "answer is required", reasons)
        require(isinstance(evidence, list), "evidence must be a list", reasons)
        if evidence:
            evidence_ids = {
                str(item.get("evidence_id") or (item.get("metadata") or {}).get("evidence_id"))
                for item in evidence
                if isinstance(item, dict)
            }
            require(any(eid and eid in answer for eid in evidence_ids), "answer must cite an evidence_id", reasons)
        else:
            require(
                "证据不足" in answer or "insufficient evidence" in answer.lower(),
                "ungrounded answer must state insufficient evidence",
                reasons,
            )
        return HarnessGradeResult(
            case_id=str(case.get("id") or ""),
            grader=self.name,
            passed=not reasons,
            score=score_from_reasons(reasons),
            reasons=reasons,
        )
