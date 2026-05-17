from __future__ import annotations

from evals.graders.base import GenericGrader, TraceCaseGrader
from evals.graders.evaluator_grader import EvaluatorLowConfidenceGrader
from evals.graders.fallback_grader import FallbackDegradedGrader
from evals.graders.guardrail_grader import GuardrailBlockedGrader
from evals.graders.llm_grader import LLMFailureGrader
from evals.graders.policy_grader import PolicyApprovalRequiredGrader
from evals.graders.repository_grader import TraceRepositoryFailureGrader
from evals.graders.retrieval_grader import RetrievalFailureGrader
from evals.graders.sandbox_grader import SandboxRejectedGrader
from evals.graders.success_grader import SuccessGrader
from evals.graders.tool_grader import ToolFailureGrader

_GRADERS: dict[str, TraceCaseGrader] = {
    grader.failure_type: grader
    for grader in (
        RetrievalFailureGrader(),
        ToolFailureGrader(),
        LLMFailureGrader(),
        SandboxRejectedGrader(),
        GuardrailBlockedGrader(),
        PolicyApprovalRequiredGrader(),
        TraceRepositoryFailureGrader(),
        EvaluatorLowConfidenceGrader(),
        FallbackDegradedGrader(),
        SuccessGrader(),
    )
}
_GENERIC = GenericGrader()


def get_grader(failure_type: str) -> TraceCaseGrader:
    return _GRADERS.get(str(failure_type or ""), _GENERIC)


def is_known_failure_type(failure_type: str) -> bool:
    return str(failure_type or "") in _GRADERS
