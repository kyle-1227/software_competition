from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any

from app.core.config import settings
from app.schemas.query import EvaluationResult
from app.schemas.trace import SpanKind
from app.services.agent_loop.retry import execute_tool_with_retry
from app.services.answer_generation import draft_answer_with_llm
from app.services.tracing.context import trace_span

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
        trace_store = getattr(services, "trace_store", None)
        trace_id = state.get("trace_id")
        async with trace_span(
            trace_store,
            trace_id,
            "evaluator.optimizer",
            SpanKind.EVALUATOR,
            inputs={
                "evidence_count": len(state.get("evidence", []) or []),
                "tool_call_count": len(state.get("tool_calls", []) or []),
                "sop_count": len(state.get("sop", []) or []),
                "has_existing_answer": bool(str(state.get("answer", "")).strip()),
            },
        ) as span:
            result = await self._generate_and_evaluate_impl(state, services)
            evaluation = result.get("evaluation", {})
            issues = evaluation.get("issues", []) if isinstance(evaluation, dict) else []
            confidence = (
                evaluation.get("confidence", 0.0)
                if isinstance(evaluation, dict)
                else 0.0
            )
            tool_calls = result.get("tool_calls", [])
            compliance_attempts = max(
                [
                    int(call.get("attempt") or 0)
                    for call in tool_calls
                    if isinstance(call, dict)
                    and call.get("tool_name") == "compliance_check"
                ]
                or [0]
            )
            compliance_degraded = any(
                isinstance(call, dict)
                and call.get("tool_name") == "compliance_check"
                and call.get("degraded")
                for call in tool_calls
            )
            compliance_success = any(
                isinstance(call, dict)
                and call.get("tool_name") == "compliance_check"
                and call.get("status") == "success"
                and not call.get("degraded")
                for call in tool_calls
            )
            span.set_metadata(
                {
                    "max_iterations": getattr(settings, "evaluator_max_iterations", 3),
                    "confidence_threshold": getattr(
                        settings, "evaluator_confidence_threshold", 0.7
                    ),
                    "iteration_count": result.get("iteration_count", 0),
                    "best_confidence": confidence,
                    "final_confidence": confidence,
                    "issues_count": len(issues),
                    "compliance_degraded": compliance_degraded,
                    "compliance_attempts": compliance_attempts,
                    "compliance_success": compliance_success,
                    "answer_regeneration_count": state.get(
                        "answer_regeneration_count", 0
                    ),
                }
            )
            span.set_outputs(
                {
                    "answer_length": len(result.get("answer", "")),
                    "confidence": confidence,
                    "issues_count": len(issues),
                    "iteration_count": result.get("iteration_count", 0),
                }
            )
            return result

    async def _generate_and_evaluate_impl(
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
                    draft_result = await draft_answer_with_llm(services, state)
                    best_answer = draft_result.get("answer", "")
            else:
                # Regenerate with feedback
                regen_state = {**state, "evaluation_feedback": feedback}
                draft_result = await draft_answer_with_llm(services, regen_state)
                best_answer = draft_result.get("answer", "")

            if not best_answer.strip():
                continue

            # Run compliance check
            compliance_retry = await execute_tool_with_retry(
                services.tool_registry,
                "compliance_check",
                {"answer": best_answer},
                trace_store=getattr(services, "trace_store", None),
                trace_id=state.get("trace_id"),
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
            "input": {"answer": _answer_summary(best_answer)},
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


def _answer_summary(answer: str) -> dict[str, Any]:
    text = str(answer or "")
    return {
        "answer_hash": sha256(text.encode("utf-8")).hexdigest(),
        "answer_length": len(text),
    }
