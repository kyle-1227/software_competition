from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass(frozen=True)
class AgentLoopPolicy:
    enabled: bool = True
    max_loop_steps: int = 8
    max_tool_retries: int = 5
    max_retrieval_retries: int = 2
    max_answer_regenerations: int = 2
    evaluator_confidence_threshold: float = 0.7
    evidence_required_for_answer: bool = True
    high_risk_requires_approval: bool = True
    retry_backoff_ms: list[int] = field(
        default_factory=lambda: [0, 100, 200, 400, 800]
    )

    @classmethod
    def from_settings(cls) -> "AgentLoopPolicy":
        return cls(
            enabled=getattr(settings, "agent_loop_enabled", True),
            max_loop_steps=getattr(settings, "agent_loop_max_steps", 8),
            max_tool_retries=getattr(settings, "agent_loop_max_tool_retries", 5),
            max_retrieval_retries=getattr(
                settings, "agent_loop_max_retrieval_retries", 2
            ),
            max_answer_regenerations=getattr(
                settings, "agent_loop_max_answer_regenerations", 2
            ),
            evaluator_confidence_threshold=getattr(
                settings, "agent_loop_confidence_threshold", 0.7
            ),
            high_risk_requires_approval=getattr(
                settings, "agent_loop_high_risk_requires_approval", True
            ),
            retry_backoff_ms=list(
                getattr(
                    settings,
                    "agent_loop_retry_backoff_ms",
                    [0, 100, 200, 400, 800],
                )
            ),
        )
