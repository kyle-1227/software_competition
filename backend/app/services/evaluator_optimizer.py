from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any

from app.core.config import settings
from app.schemas.query import EvaluationResult
from app.schemas.trace import SpanKind
from app.services.answer_generation import draft_answer_with_llm
from app.services.tool_registry import ToolResult
from app.services.tracing.context import trace_span

logger = logging.getLogger(__name__)


class EvaluatorOptimizer:
    """Evaluator-Optimizer loop: generate, evaluate, feed back, regenerate."""

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
            compliance_success = any(
                isinstance(call, dict)
                and call.get("tool_name") == "compliance_check"
                and call.get("status") == "success"
                and bool(call.get("brokered"))
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
                    "compliance_attempts": compliance_attempts,
                    "compliance_success": compliance_success,
                    "answer_regeneration_count": state.get(
                        "answer_regeneration_count", 0
                    ),
                    "feedback_used": bool(result.get("feedback_used")),
                    "llm_generation_failed": bool(
                        result.get("llm_generation_failed", False)
                    ),
                    "llm_model": result.get("llm_model"),
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

        best_answer = str(state.get("answer", "") or "")
        best_eval: EvaluationResult | None = None
        best_confidence = 0.0
        iteration_count = 0
        compliance_result: ToolResult | None = None
        generation_failed = False
        feedback_used = False
        llm_model = state.get("llm_model")
        llm_usage = state.get("llm_usage")
        warnings = list(state.get("warnings", [])) if isinstance(state.get("warnings"), list) else []

        for iteration in range(max_iterations):
            iteration_count = iteration + 1
            feedback = best_eval.feedback if best_eval else ""
            generation_state = {
                **state,
                "previous_answer": best_answer,
                "evaluation_feedback": feedback,
                "generation_iteration": iteration_count,
            }

            if iteration == 0 and best_answer.strip():
                generation_failed = False
            else:
                if feedback:
                    feedback_used = True
                draft_result = await draft_answer_with_llm(services, generation_state)
                best_answer = str(draft_result.get("answer", "") or "")
                generation_failed = bool(draft_result.get("llm_generation_failed"))
                llm_model = draft_result.get("llm_model")
                llm_usage = draft_result.get("llm_usage")
                warnings = _merge_warnings(warnings, draft_result.get("warnings", []))

            if not best_answer.strip():
                if generation_failed:
                    break
                continue

            compliance_result = await _run_compliance_check(
                services,
                "compliance_check",
                {"answer": best_answer},
                trace_id=state.get("trace_id"),
                risk_level=str(state.get("risk_level") or "unknown"),
            )

            evaluation = await self._evaluator.evaluate(
                best_answer, evidence, tool_calls, sop
            )

            if evaluation.confidence > best_confidence:
                best_confidence = evaluation.confidence
                best_eval = evaluation

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
                issues=(
                    ["llm_generation_failed"]
                    if generation_failed
                    else ["evaluator did not produce a valid result"]
                ),
                feedback="llm_generation_failed" if generation_failed else None,
            )

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
            "degraded": False,
            "attempt": 1 if compliance_result is not None else None,
            "brokered": (
                bool(compliance_result.metadata.get("brokered"))
                if compliance_result is not None
                else False
            ),
        }

        return {
            "answer": best_answer,
            "evaluation": best_eval.model_dump(mode="json"),
            "iteration_count": iteration_count,
            "evaluation_feedback": best_eval.feedback or "",
            "feedback_used": feedback_used,
            "tool_calls": state.get("tool_calls", []) + [compliance_tc],
            "degradation_events": state.get("degradation_events", []),
            "warnings": warnings,
            "llm_model": llm_model,
            "llm_usage": llm_usage,
            "llm_generation_failed": generation_failed,
        }


def _answer_summary(answer: str) -> dict[str, Any]:
    text = str(answer or "")
    return {
        "answer_hash": sha256(text.encode("utf-8")).hexdigest(),
        "answer_length": len(text),
    }


async def _run_compliance_check(
    services: Any,
    name: str,
    payload: dict[str, Any],
    *,
    trace_id: str | None,
    risk_level: str,
) -> ToolResult:
    broker = getattr(services, "tool_broker", None)
    if broker is None:
        return ToolResult(
            tool_name=name,
            success=False,
            error="ToolBroker unavailable for compliance_check",
            metadata={"brokered": False},
        )
    return await broker.execute(
        name,
        payload,
        caller="evaluator_optimizer",
        risk_level=risk_level,
        trace_id=trace_id,
    )


def _merge_warnings(current: list[str], extra: Any) -> list[str]:
    result = list(current)
    if isinstance(extra, list):
        for item in extra:
            text = str(item)
            if text not in result:
                result.append(text)
    return result
