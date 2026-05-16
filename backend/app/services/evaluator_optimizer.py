from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.schemas.query import EvaluationResult
from app.services.agent_loop.retry import execute_tool_with_retry

logger = logging.getLogger(__name__)


class EvaluatorOptimizer:
    """Evaluator-Optimizer 循环：生成→评估→反馈→再生成。

    最多 N 轮迭代，每轮评估后如果置信度低于阈值且有 issues，
    将反馈注入上下文重新生成。循环在节点内部完成，图保持 DAG。
    """

    def __init__(self, evaluator: Any) -> None:
        self._evaluator = evaluator

    async def generate_and_evaluate(
        self,
        state: dict[str, Any],
        services: Any,
    ) -> dict[str, Any]:
        max_iterations = getattr(settings, "evaluator_max_iterations", 3)
        confidence_threshold = getattr(
            settings, "evaluator_confidence_threshold", 0.7
        )

        evidence = [
            item for item in state.get("evidence", []) if isinstance(item, dict)
        ]
        tool_calls = [
            item for item in state.get("tool_calls", []) if isinstance(item, dict)
        ]
        sop = state.get("sop", [])

        best_answer = state.get("answer", "")
        best_eval: EvaluationResult | None = None
        best_confidence = 0.0
        iteration_count = 0
        compliance_retry = None
        compliance_result = None

        for iteration in range(max_iterations):
            iteration_count = iteration + 1
            feedback = best_eval.feedback if best_eval else ""

            if iteration == 0:
                # First generation: use existing answer or generate
                if not best_answer.strip():
                    from app.services.graph.graph_builder import (
                        _draft_answer_with_llm,
                    )
                    draft_result = await _draft_answer_with_llm(services, state)
                    best_answer = draft_result.get("answer", "")
            else:
                # Regenerate with feedback
                regen_state = {**state, "evaluation_feedback": feedback}
                from app.services.graph.graph_builder import (
                    _draft_answer_with_llm,
                )
                draft_result = await _draft_answer_with_llm(services, regen_state)
                best_answer = draft_result.get("answer", "")

            if not best_answer.strip():
                continue

            # Run compliance check
            compliance_retry = await execute_tool_with_retry(
                services.tool_registry,
                "compliance_check",
                {"answer": best_answer},
            )
            compliance_result = compliance_retry.result

            # Evaluate
            evaluation = await self._evaluator.evaluate(
                best_answer, evidence, tool_calls, sop
            )

            # Track best
            if evaluation.confidence > best_confidence:
                best_confidence = evaluation.confidence
                best_eval = evaluation

            # Stop condition
            if (
                evaluation.confidence >= confidence_threshold
                and not evaluation.issues
            ):
                break

        if best_eval is None:
            best_eval = EvaluationResult(
                is_safe=False,
                is_compliant=False,
                confidence=0.0,
                issues=["评估器未能产生有效的评估结果"],
            )

        # Build tool_call for compliance check
        compliance_tc = {
            "tool_name": "compliance_check",
            "input": {"answer": best_answer[:200]},
            "output": (
                compliance_result.data
                if compliance_result is not None and compliance_result.success
                else {"error": compliance_result.error if compliance_result else "unknown"}
            ),
            "status": (
                "success"
                if compliance_result is not None and compliance_result.success
                else "failed"
            ),
            "duration_ms": (
                compliance_result.metadata.get("duration_ms")
                if compliance_result is not None
                else None
            ),
            "degraded": compliance_retry.degraded if compliance_retry is not None else False,
            "attempt": compliance_retry.attempts if compliance_retry is not None else None,
        }
        retry_tool_calls = compliance_retry.tool_calls if compliance_retry is not None else []
        retry_events = (
            compliance_retry.degradation_events if compliance_retry is not None else []
        )

        return {
            "answer": best_answer,
            "evaluation": best_eval.model_dump(mode="json"),
            "iteration_count": iteration_count,
            "evaluation_feedback": best_eval.feedback or "",
            "tool_calls": state.get("tool_calls", []) + retry_tool_calls + [compliance_tc],
            "degradation_events": state.get("degradation_events", [])
            + retry_events,
        }
