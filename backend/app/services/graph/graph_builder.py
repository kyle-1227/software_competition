from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings
from app.schemas.query import QueryResponse
from app.schemas.trace import SpanKind
from app.services.agent_loop.actions import AgentLoopAction
from app.services.agent_loop.controller import (
    AgentLoopController,
    approval_scope_hash,
    has_effective_evidence,
    placeholder_used_in_state,
)
from app.services.agent_loop.policy import AgentLoopPolicy
from app.services.agent_loop.retry import execute_tool_with_retry
from app.services.graph.state import HarnessState
from app.services.tracing.context import trace_span
from app.services.tracing.helpers import summarize_span_payload

logger = logging.getLogger(__name__)

def build_harness_graph(services) -> Any:
    """Build the Orchestrator-Workers + Bounded Agent Loop graph."""
    checkpointer, warning = _build_checkpointer()
    if warning:
        logger.warning(warning)
        services.warnings.append(warning)

    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - optional dependency fallback
        logger.warning("LangGraph unavailable, using fallback runner: %s", exc)
        return _build_fallback_graph(services)

    return _build_new_graph(services, StateGraph, END, checkpointer)


def _build_new_graph(services, StateGraph, END, checkpointer) -> Any:
    """Build the Orchestrator-Workers + Bounded Agent Loop graph."""
    use_og = getattr(settings, "use_output_guardrail", False)
    graph = StateGraph(HarnessState)
    nodes = _build_new_nodes(services)

    graph.add_node("intake_node", nodes["intake_node"])
    graph.add_node("input_guardrail_node", nodes["input_guardrail_node"])
    graph.add_node("memory_load_node", nodes["memory_load_node"])
    graph.add_node("orchestrator_node", nodes["orchestrator_node"])
    graph.add_node("worker_executor_node", nodes["worker_executor_node"])
    graph.add_node("loop_decision_node", nodes["loop_decision_node"])
    graph.add_node("retrieval_retry_node", nodes["retrieval_retry_node"])
    graph.add_node("approval_node", nodes["approval_node"])
    graph.add_node("clarification_node", nodes["clarification_node"])
    graph.add_node("fail_safe_node", nodes["fail_safe_node"])
    graph.add_node("evaluator_optimizer_node", nodes["evaluator_optimizer_node"])
    graph.add_node("post_eval_loop_decision_node", nodes["post_eval_loop_decision_node"])
    graph.add_node("answer_regeneration_node", nodes["answer_regeneration_node"])
    graph.add_node("final_verifier_node", nodes["final_verifier_node"])

    if use_og:
        graph.add_node("output_guardrail_node", nodes["output_guardrail_node"])

    graph.add_node("trace_node", nodes["trace_node"])
    graph.add_node("memory_save_node", nodes["memory_save_node"])
    graph.add_node("finalize_node", nodes["finalize_node"])

    graph.set_entry_point("intake_node")
    graph.add_edge("intake_node", "input_guardrail_node")

    # Input guardrail: if blocked, skip directly to finalize
    graph.add_conditional_edges(
        "input_guardrail_node",
        nodes["route_after_guardrail"],
        {
            "blocked": "finalize_node",
            "continue": "memory_load_node",
        },
    )

    graph.add_edge("memory_load_node", "orchestrator_node")
    graph.add_edge("orchestrator_node", "worker_executor_node")
    graph.add_edge("worker_executor_node", "loop_decision_node")

    graph.add_conditional_edges(
        "loop_decision_node",
        nodes["route_loop_decision"],
        {
            "retry_retrieval": "retrieval_retry_node",
            "approval": "approval_node",
            "clarification": "clarification_node",
            "fail_safe": "fail_safe_node",
            "evaluate": "evaluator_optimizer_node",
        },
    )
    graph.add_conditional_edges(
        "retrieval_retry_node",
        nodes["route_after_retrieval_retry"],
        {
            "evaluate": "evaluator_optimizer_node",
            "decide": "loop_decision_node",
        },
    )
    graph.add_edge("evaluator_optimizer_node", "post_eval_loop_decision_node")
    graph.add_conditional_edges(
        "post_eval_loop_decision_node",
        nodes["route_post_eval_loop_decision"],
        {
            "regenerate": "answer_regeneration_node",
            "approval": "approval_node",
            "clarification": "clarification_node",
            "fail_safe": "fail_safe_node",
            "output": "final_verifier_node",
        },
    )
    graph.add_edge("answer_regeneration_node", "evaluator_optimizer_node")
    graph.add_edge(
        "final_verifier_node",
        "output_guardrail_node" if use_og else "trace_node",
    )
    if use_og:
        graph.add_edge("approval_node", "trace_node")
        graph.add_edge("clarification_node", "output_guardrail_node")
        graph.add_edge("fail_safe_node", "output_guardrail_node")
        graph.add_edge("output_guardrail_node", "trace_node")
    else:
        graph.add_edge("approval_node", "trace_node")
        graph.add_edge("clarification_node", "trace_node")
        graph.add_edge("fail_safe_node", "trace_node")

    graph.add_edge("trace_node", "memory_save_node")
    graph.add_edge("memory_save_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph.compile(checkpointer=checkpointer)


def _build_shared_nodes(services) -> dict[str, Any]:
    async def intake_node(state: dict[str, Any]) -> dict[str, Any]:
        session_id = (
            state.get("session_id")
            or state.get("device_model")
            or state.get("device_name")
            or "default"
        )
        runtime_request = state.get("runtime_request")
        if not isinstance(runtime_request, dict):
            runtime_request = {}
        return {
            "question": state["question"],
            "device_name": state.get("device_name"),
            "device_model": state.get("device_model"),
            "session_id": session_id,
            "trace_id": state.get("trace_id")
            or services.trace_store.start_trace(
                session_id=session_id,
                question=state["question"],
                metadata={
                    "device_name": state.get("device_name"),
                    "device_model": state.get("device_model"),
                    "feature_flags": {
                        "agent_loop_enabled": getattr(
                            settings, "agent_loop_enabled", True
                        ),
                        "use_input_guardrail": getattr(
                            settings, "use_input_guardrail", True
                        ),
                        "use_output_guardrail": getattr(
                            settings, "use_output_guardrail", True
                        ),
                        "use_real_ai_coding": getattr(
                            settings, "use_real_ai_coding", True
                        ),
                    },
                    "runtime": {
                        "request_id": runtime_request.get("request_id"),
                        "source": runtime_request.get("source"),
                    },
                    "llm_model": getattr(settings, "deepseek_model", None),
                    "embedding_model": getattr(settings, "embedding_model", None),
                    "reranker_model": getattr(settings, "reranker_model", None),
                },
            ),
            "errors": [],
            "warnings": [],
            "tool_calls": [],
            "evidence": [],
            "memory": [],
            "plan": [],
            "answer": "",
            "sop": [],
            "llm_model": None,
            "llm_usage": None,
            "ai_coding": None,
            "sandbox_result": None,
            "evaluation": None,
            "loop_decision_count": 0,
            "loop_history": [],
            "tool_retry_counts": {},
            "retrieval_retry_count": 0,
            "answer_regeneration_count": 0,
            "degradation_events": [],
            "requires_human_approval": False,
            "approval_reason": None,
            "approval": None,
            "approval_decision": None,
            "approved_approval_id": None,
            "approval_scope_hash": None,
            "status": "completed",
            "clarification_question": None,
            "fail_safe_reason": None,
        }

    async def memory_load_node(state: dict[str, Any]) -> dict[str, Any]:
        history = services.memory_store.get_history(state["session_id"])
        return {"memory": history}

    async def trace_node(state: dict[str, Any]) -> dict[str, Any]:
        trace_id = state.get("trace_id", "trace-placeholder")
        services.trace_store.record_plan(trace_id, state.get("plan", []))
        services.trace_store.record_evidence(trace_id, state.get("evidence", []))
        services.trace_store.record_answer(trace_id, state.get("answer", ""))

        # Embedding / Reranker metadata for observability
        _record_pipeline_meta(services, trace_id, state)

        if state.get("evaluation"):
            from app.schemas.query import EvaluationResult

            services.trace_store.record_evaluation(
                trace_id, EvaluationResult(**state["evaluation"])
            )
        if state.get("sandbox_result"):
            services.trace_store.record_sandbox_result(trace_id, state["sandbox_result"])
        return {}

    async def memory_save_node(state: dict[str, Any]) -> dict[str, Any]:
        if (
            state.get("status") == "pending_approval"
            or state.get("verification_passed") is not True
            or state.get("verification_skipped_reason")
        ):
            return {"memory": services.memory_store.get_history(state["session_id"])}
        summary = {
            "question": state.get("question"),
            "answer": state.get("answer"),
            "evidence_summary": [item.get("snippet", "") for item in state.get("evidence", [])],
            "tool_calls_summary": [item.get("tool_name", "") for item in state.get("tool_calls", [])],
            "sandbox_result_summary": state.get("sandbox_result"),
            "evaluation": state.get("evaluation"),
        }
        services.memory_store.add_trace(state["session_id"], summary)
        return {"memory": services.memory_store.get_history(state["session_id"])}

    async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
        errors = state.get("errors", [])
        # If guardrail blocked the request, return a clear rejection
        if state.get("guardrail_passed") is False and any(
            "blocked" in str(e).lower() or "不适当" in str(e) or "超出" in str(e)
            for e in errors
        ):
            rejection = {
                "answer": f"抱歉，无法处理此请求。{'；'.join(errors)}",
                "plan": [],
                "evidence": [],
                "tool_calls": [],
                "evaluation": None,
                "trace_id": state.get("trace_id"),
                "sop": [],
                "memory": state.get("memory", []),
                "ai_coding": None,
                "llm_usage": None,
                "llm_model": None,
                "status": "completed",
                "approval": None,
            }
            return {"response": rejection, **rejection}

        response = {
            "answer": state.get("answer", ""),
            "plan": state.get("plan", []),
            "evidence": state.get("evidence", []),
            "tool_calls": state.get("tool_calls", []),
            "evaluation": state.get("evaluation"),
            "trace_id": state.get("trace_id"),
            "sop": state.get(
                "sop",
                [
                    "停机并断电，确认设备处于安全状态。",
                    "佩戴防护用品，检查现场风险。",
                    "依据手册证据逐项排查，不直接执行高风险操作。",
                    "记录现象、处理步骤和结果，必要时提交知识审核。",
                ],
            ),
            "memory": state.get("memory", []),
            "ai_coding": state.get("ai_coding"),
            "llm_usage": state.get("llm_usage"),
            "llm_model": state.get("llm_model"),
            "status": state.get("status") or "completed",
            "approval": state.get("approval"),
        }
        if not response["sop"]:
            response["sop"] = [
                "停机并断电，确认设备处于安全状态。",
                "佩戴防护用品，检查现场风险。",
                "依据手册证据逐项排查，不直接执行高风险操作。",
                "记录现象、处理步骤和结果，必要时提交知识审核。",
            ]
        return {"response": response, **response}

    finalize_node_impl = finalize_node

    async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
        trace_id = state.get("trace_id")
        try:
            async with trace_span(
                getattr(services, "trace_store", None),
                trace_id,
                "node.finalize",
                SpanKind.NODE,
                inputs=_node_span_metadata(state, "finalize"),
                metadata=_node_span_metadata(state, "finalize"),
            ) as span:
                update = await finalize_node_impl(state)
                span.set_metadata(_node_span_metadata({**state, **update}, "finalize"))
                span.set_outputs(update)
                return update
        finally:
            close_trace = getattr(
                getattr(services, "trace_store", None),
                "close_trace",
                None,
            )
            if trace_id and callable(close_trace):
                close_trace(trace_id, status="error" if state.get("errors") else None)

    return {
        "intake_node": intake_node,
        "memory_load_node": _wrap_node_with_span(
            services, "memory_load", SpanKind.MEMORY, memory_load_node
        ),
        "trace_node": _wrap_node_with_span(
            services, "trace", SpanKind.NODE, trace_node
        ),
        "memory_save_node": _wrap_node_with_span(
            services, "memory_save", SpanKind.MEMORY, memory_save_node
        ),
        "finalize_node": finalize_node,
    }


def _ensure_new_services(services) -> None:
    """Lazily construct Phase 1-2 services on the services namespace."""
    if not hasattr(services, "policy_engine") or services.policy_engine is None:
        from app.policy.engine import PolicyEngine

        services.policy_engine = PolicyEngine()
    if not hasattr(services, "tool_registry") or services.tool_registry is None:
        from app.services.tool_registry import ToolRegistry

        services.tool_registry = ToolRegistry()
    if not hasattr(services, "tool_broker") or services.tool_broker is None:
        from app.tools.broker import ToolBroker
        from app.tools.manifest import build_default_tool_manifests

        services.tool_broker = ToolBroker(
            tool_registry=services.tool_registry,
            manifests=build_default_tool_manifests(services.tool_registry),
            policy_engine=services.policy_engine,
            trace_store=getattr(services, "trace_store", None),
        )

    if not hasattr(services, "orchestrator") or services.orchestrator is None:
        from app.services.orchestrator import Orchestrator
        from app.services.guardrails.input_guard import InputGuardrail
        from app.services.workers.dispatcher import WorkerDispatcher
        from app.services.workers.fault_triage import FaultTriageWorker
        from app.services.workers.sop_guidance import SOPGuidanceWorker
        from app.services.workers.ai_coding import AICodingWorker

        model_gateway = getattr(services, "model_gateway", None)
        legacy_llm_client = getattr(services, "llm_client", None)
        llm_entrypoint = model_gateway or legacy_llm_client
        services.orchestrator = Orchestrator(
            llm_client=legacy_llm_client,
            model_gateway=model_gateway,
        )
        services.input_guardrail = InputGuardrail(llm_client=llm_entrypoint)

        workers = {
            "fault_triage": FaultTriageWorker(),
            "sop_guidance": SOPGuidanceWorker(),
            "ai_coding": AICodingWorker(),
        }
        services.worker_dispatcher = WorkerDispatcher(workers)

    # Phase 2: evaluator services (lazy)
    if not hasattr(services, "llm_evaluator") or services.llm_evaluator is None:
        from app.services.evaluator_llm import LLMEvaluator
        from app.services.evaluator_optimizer import EvaluatorOptimizer

        llm_client = getattr(services, "model_gateway", None) or getattr(
            services, "llm_client", None
        )
        fallback = getattr(services, "evaluator", None)
        services.llm_evaluator = LLMEvaluator(
            llm_client=llm_client,
            fallback_evaluator=fallback,
        )
        services.evaluator_optimizer = EvaluatorOptimizer(
            evaluator=services.llm_evaluator,
        )

    # Phase 3: output guardrail (lazy)
    if not hasattr(services, "output_guardrail") or services.output_guardrail is None:
        from app.services.guardrails.output_guard import OutputGuardrail

        llm_client = getattr(services, "model_gateway", None) or getattr(
            services, "llm_client", None
        )
        services.output_guardrail = OutputGuardrail(llm_client=llm_client)

    if not hasattr(services, "agent_loop_controller") or services.agent_loop_controller is None:
        services.agent_loop_controller = AgentLoopController()
    if not hasattr(services, "agent_loop_policy") or services.agent_loop_policy is None:
        services.agent_loop_policy = AgentLoopPolicy.from_settings()
    if not hasattr(services, "approval_store") or services.approval_store is None:
        from app.services.approval_store import ApprovalStore

        services.approval_store = ApprovalStore()
    if not hasattr(services, "final_verifier") or services.final_verifier is None:
        from app.verification.final_verifier import DiagnosticFinalVerifier

        services.final_verifier = DiagnosticFinalVerifier()
    if not hasattr(services, "terminal_verifier") or services.terminal_verifier is None:
        from app.verification.final_verifier import TerminalStateVerifier

        services.terminal_verifier = TerminalStateVerifier()


def _wrap_node_with_span(services, node_name: str, kind: SpanKind, node):
    async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        async with trace_span(
            getattr(services, "trace_store", None),
            state.get("trace_id"),
            f"node.{node_name}",
            kind,
            inputs=_node_span_metadata(state, node_name),
            metadata=_node_span_metadata(state, node_name),
        ) as span:
            update = await node(state)
            merged_state = {**state, **(update or {})}
            span.set_metadata(_node_span_metadata(merged_state, node_name))
            span.set_outputs(update or {})
            return update

    return wrapped


def _node_span_metadata(state: dict[str, Any], node_name: str) -> dict[str, Any]:
    evaluation = state.get("evaluation")
    confidence = evaluation.get("confidence") if isinstance(evaluation, dict) else None
    return {
        "node_name": node_name,
        "evidence_count": len(state.get("evidence", []) or []),
        "tool_call_count": len(state.get("tool_calls", []) or []),
        "warning_count": len(state.get("warnings", []) or []),
        "degradation_event_count": len(state.get("degradation_events", []) or []),
        "loop_decision_count": state.get("loop_decision_count", 0),
        "retrieval_retry_count": state.get("retrieval_retry_count", 0),
        "answer_regeneration_count": state.get("answer_regeneration_count", 0),
        "decision_action": _decision_action(state),
        "confidence": confidence,
        "requires_human_approval": bool(state.get("requires_human_approval", False)),
    }


def _retrieved_pages(evidence: list[dict[str, Any]]) -> list[Any]:
    pages: list[Any] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        page = item.get("page")
        if page is not None and page not in pages:
            pages.append(page)
    return pages


def _preview(value: Any, limit: int = 120) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _build_new_nodes(services) -> dict[str, Any]:
    """Build node implementations for the Orchestrator-Workers graph.

    Reuses intake_node, memory_load_node, trace_node, memory_save_node, and
    finalize_node from _build_shared_nodes for maximum code sharing.
    """
    _ensure_new_services(services)
    shared_nodes = _build_shared_nodes(services)

    async def input_guardrail_node(state: dict[str, Any]) -> dict[str, Any]:
        result = await services.input_guardrail.check(
            state.get("question", ""),
            state.get("device_name"),
        )
        return {
            "guardrail_passed": result.passed,
            "errors": state.get("errors", [])
            + ([] if result.passed else [result.reason or "输入护栏拦截"]),
        }

    def route_after_guardrail(state: dict[str, Any]) -> str:
        return "continue" if state.get("guardrail_passed") else "blocked"

    async def orchestrator_node(state: dict[str, Any]) -> dict[str, Any]:
        decision = await services.orchestrator.classify_and_plan(
            state["question"],
            state.get("device_name"),
            state.get("memory", []),
        )
        return {
            "intent": decision.intent,
            "plan": decision.dynamic_plan,
            "task_plan": decision.task_plan,
            "risk_level": decision.risk_level,
            "allowed_tools": decision.allowed_tools,
            "needs_ai_coding": "ai_coding" in decision.workers,
            "_orchestrator_decision": decision,
        }

    async def worker_executor_node(state: dict[str, Any]) -> dict[str, Any]:
        decision = state.get("_orchestrator_decision")
        if decision is None:
            # Fallback: treat as fault_triage + sop_guidance
            from app.schemas.orchestrator import OrchestratorDecision
            decision = OrchestratorDecision(
                intent="general",
                workers=["fault_triage"],
                reasoning="fallback",
                priority="safety_first",
            )

        worker_results = await services.worker_dispatcher.dispatch(
            decision, state, services
        )

        worker_outputs: list[dict[str, Any]] = []
        merged_evidence: list[dict[str, Any]] = _dedupe_evidence(state.get("evidence", []))
        merged_tool_calls: list[dict[str, Any]] = _dedupe_tool_calls(state.get("tool_calls", []))
        merged_sop: list[str] = _dedupe_strings(state.get("sop", []))
        merged_warnings: list[str] = _dedupe_strings(state.get("warnings", []))
        merged_degradation_events: list[dict[str, Any]] = list(
            state.get("degradation_events", [])
            if isinstance(state.get("degradation_events"), list)
            else []
        )
        merged_ai_coding: dict[str, Any] | None = state.get("ai_coding")
        merged_sandbox: dict[str, Any] | None = state.get("sandbox_result")
        requires_human_approval = bool(state.get("requires_human_approval", False))
        approval_reason = state.get("approval_reason")
        worker_evidence: list[dict[str, Any]] = []

        for result in worker_results:
            if not isinstance(result, dict):
                continue

            w_outputs = result.get("worker_outputs", [])
            if isinstance(w_outputs, list):
                worker_outputs.extend(item for item in w_outputs if isinstance(item, dict))
            else:
                worker_outputs.append(result)

            w_evidence = result.get("evidence", [])
            if isinstance(w_evidence, list):
                evidence_items = [item for item in w_evidence if isinstance(item, dict)]
                worker_evidence.extend(evidence_items)
                merged_evidence = _dedupe_evidence(merged_evidence + evidence_items)

            w_tool_calls = result.get("tool_calls", [])
            if isinstance(w_tool_calls, list):
                merged_tool_calls = _dedupe_tool_calls(merged_tool_calls + w_tool_calls)

            w_sop = result.get("sop", result.get("sop_steps", []))
            if isinstance(w_sop, list):
                merged_sop = _dedupe_strings(merged_sop + w_sop)

            w_ai = result.get("ai_coding")
            if isinstance(w_ai, dict):
                merged_ai_coding = w_ai
            w_sandbox = result.get("sandbox_result")
            if isinstance(w_sandbox, dict):
                merged_sandbox = w_sandbox

            w_warnings = result.get("warnings", [])
            if isinstance(w_warnings, list):
                merged_warnings = _dedupe_strings(merged_warnings + w_warnings)

            w_events = result.get("degradation_events", [])
            if isinstance(w_events, list):
                merged_degradation_events.extend(
                    event for event in w_events if isinstance(event, dict)
                )
            if result.get("requires_human_approval"):
                requires_human_approval = True
                approval_reason = result.get("approval_reason") or approval_reason

        update: dict[str, Any] = {
            "worker_outputs": worker_outputs,
            "evidence": merged_evidence,
            "tool_calls": merged_tool_calls,
            "warnings": merged_warnings,
            "degradation_events": merged_degradation_events,
            "requires_human_approval": requires_human_approval,
            "approval_reason": approval_reason,
        }

        if not merged_evidence and not worker_evidence and _decision_needs_evidence(decision):
            fallback = await _run_manual_lookup_with_retry({**state, **update}, services)
            fallback_evidence = fallback.get("evidence", [])
            if isinstance(fallback_evidence, list):
                update["evidence"] = _dedupe_evidence(fallback_evidence)
            fallback_tool_calls = fallback.get("tool_calls", [])
            if isinstance(fallback_tool_calls, list):
                update["tool_calls"] = _dedupe_tool_calls(fallback_tool_calls)
            fallback_warnings = fallback.get("warnings", [])
            if isinstance(fallback_warnings, list):
                update["warnings"] = _dedupe_strings(fallback_warnings)
            fallback_events = fallback.get("degradation_events", [])
            if isinstance(fallback_events, list):
                update["degradation_events"] = fallback_events

        if merged_sop:
            update["sop"] = merged_sop
        if merged_ai_coding:
            update["ai_coding"] = merged_ai_coding
        if merged_sandbox:
            update["sandbox_result"] = merged_sandbox

        return update

    async def evaluator_optimizer_node(state: dict[str, Any]) -> dict[str, Any]:
        return await services.evaluator_optimizer.generate_and_evaluate(
            state, services
        )

    async def output_guardrail_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("status") == "pending_approval":
            return {}
        if state.get("verification_passed") is False:
            return {}
        answer = state.get("answer", "")
        evaluation = state.get("evaluation")
        result = await services.output_guardrail.check(answer, evaluation)
        modified = services.output_guardrail.apply_fixes(answer, result)
        return {
            "output_guardrail_issues": (
                [result.reason] if result.reason and not result.passed else []
            ),
            "answer": modified if modified != answer else answer,
        }

    async def final_verifier_node(state: dict[str, Any]) -> dict[str, Any]:
        terminal_update = services.terminal_verifier.terminal_skip_update(state)
        if terminal_update is not None:
            return terminal_update
        result = services.final_verifier.verify(state)
        if result.passed:
            return {
                "status": "completed",
                "verification_passed": True,
                "verification_issues": [],
                "verification_skipped_reason": None,
            }
        return services.final_verifier.failure_update(result.issues)

    async def loop_decision_node(state: dict[str, Any]) -> dict[str, Any]:
        return _loop_decision_update(state, services)

    async def post_eval_loop_decision_node(state: dict[str, Any]) -> dict[str, Any]:
        return _loop_decision_update(state, services)

    async def retrieval_retry_node(state: dict[str, Any]) -> dict[str, Any]:
        retry_count = int(state.get("retrieval_retry_count", 0) or 0) + 1
        lookup = await _run_manual_lookup_with_retry(
            state,
            services,
            question_override=_build_retry_query(state),
        )
        degradation_events = list(lookup.get("degradation_events", []))
        warnings = _dedupe_strings(lookup.get("warnings", []))
        if not has_effective_evidence(lookup):
            warnings = _dedupe_strings(
                warnings + ["retrieval retry returned no effective manual evidence"]
            )
            degradation_events.append(
                {
                    "type": "retrieval_degraded",
                    "tool_name": "manual_lookup",
                    "attempts": lookup.get("_manual_lookup_attempts", 0),
                    "reason": "retrieval retry returned no effective evidence",
                    "fallback": "clarification or fail-safe",
                }
            )

        return {
            "question": state.get("question", ""),
            "retrieval_retry_count": retry_count,
            "evidence": lookup.get("evidence", []),
            "tool_calls": lookup.get("tool_calls", []),
            "warnings": warnings,
            "degradation_events": degradation_events,
        }

    async def answer_regeneration_node(state: dict[str, Any]) -> dict[str, Any]:
        count = int(state.get("answer_regeneration_count", 0) or 0) + 1
        history = _append_loop_history(
            state,
            {
                "action": AgentLoopAction.REGENERATE_ANSWER.value,
                "reason": "Regenerating answer after low confidence evaluation",
                "count": count,
            },
        )
        return {
            "answer": "",
            "answer_regeneration_count": count,
            "loop_history": history,
            "warnings": _dedupe_strings(
                state.get("warnings", []) + ["低置信度答案触发有限再生成"]
            ),
        }

    async def approval_node(state: dict[str, Any]) -> dict[str, Any]:
        reason = state.get("approval_reason") or _loop_reason(state)
        answer = (
            "该请求涉及高风险操作或当前手册证据不足，建议人工确认后继续。"
            "在人工复核前，我不能提供具体拆卸、刷写、带电操作或直接更换步骤。"
        )
        return {
            "answer": answer,
            "requires_human_approval": True,
            "approval_reason": reason,
            "warnings": _dedupe_strings(state.get("warnings", []) + [str(reason)]),
        }

    async def clarification_node(state: dict[str, Any]) -> dict[str, Any]:
        question = (
            "目前证据不足，无法给出确定结论。请补充：设备型号、故障发生时机、"
            "冷车/热车状态、是否有异响、是否已有测量值。"
        )
        return {
            "answer": question,
            "clarification_question": question,
            "warnings": _dedupe_strings(
                state.get("warnings", []) + ["证据不足，已请求补充信息"]
            ),
        }

    async def fail_safe_node(state: dict[str, Any]) -> dict[str, Any]:
        reason = _loop_reason(state) or "Agent Loop 无法可靠完成诊断"
        answer = (
            "系统无法可靠完成本次诊断，不能编造参数或维修结论。"
            "请补充手册证据、设备型号和故障现象，或交由具备资质的维修人员人工检修。"
        )
        return {
            "answer": answer,
            "fail_safe_reason": reason,
            "evaluation": {
                "is_safe": False,
                "is_compliant": False,
                "confidence": 0.0,
                "issues": [str(reason)],
                "feedback": "fail-safe",
            },
            "warnings": _dedupe_strings(state.get("warnings", []) + [str(reason)]),
        }

    async def approval_node(state: dict[str, Any]) -> dict[str, Any]:
        reason = state.get("approval_reason") or _loop_reason(state)
        scope_hash = approval_scope_hash(state)
        record = services.approval_store.create(
            reason=str(reason) if reason else None,
            risk_level=state.get("risk_level"),
            trace_id=state.get("trace_id"),
            session_id=state.get("session_id"),
            approval_scope_hash=scope_hash,
            state_snapshot={
                **state,
                "approval_scope_hash": scope_hash,
                "status": "pending_approval",
            },
        )
        approval = {
            "approval_id": record.approval_id,
            "status": record.status,
            "reason": record.reason,
            "risk_level": record.risk_level,
            "trace_id": record.trace_id,
            "approval_scope_hash": record.approval_scope_hash,
        }
        return {
            "answer": "This request is waiting for human approval. No diagnostic answer has been finalized.",
            "status": "pending_approval",
            "approval": approval,
            "pending_approval": True,
            "approval_scope_hash": scope_hash,
            "requires_human_approval": True,
            "approval_reason": reason,
            "warnings": _dedupe_strings(state.get("warnings", []) + [str(reason)]),
        }

    def route_loop_decision(state: dict[str, Any]) -> str:
        return _route_loop_decision_value(state)

    def route_post_eval_loop_decision(state: dict[str, Any]) -> str:
        decision = _decision_action(state)
        if decision == AgentLoopAction.REGENERATE_ANSWER.value:
            return "regenerate"
        if decision == AgentLoopAction.REQUIRE_APPROVAL.value:
            return "approval"
        if decision == AgentLoopAction.ASK_CLARIFICATION.value:
            return "clarification"
        if decision == AgentLoopAction.FAIL_SAFE.value:
            return "fail_safe"
        return "output"

    def route_after_retrieval_retry(state: dict[str, Any]) -> str:
        return "evaluate" if has_effective_evidence(state) else "decide"

    return {
        "intake_node": shared_nodes["intake_node"],
        "input_guardrail_node": _wrap_node_with_span(
            services, "input_guardrail", SpanKind.GUARDRAIL, input_guardrail_node
        ),
        "memory_load_node": shared_nodes["memory_load_node"],
        "orchestrator_node": _wrap_node_with_span(
            services, "orchestrator", SpanKind.NODE, orchestrator_node
        ),
        "worker_executor_node": _wrap_node_with_span(
            services, "worker_executor", SpanKind.NODE, worker_executor_node
        ),
        "loop_decision_node": _wrap_node_with_span(
            services, "loop_decision", SpanKind.NODE, loop_decision_node
        ),
        "retrieval_retry_node": _wrap_node_with_span(
            services, "retrieval_retry", SpanKind.NODE, retrieval_retry_node
        ),
        "evaluator_optimizer_node": _wrap_node_with_span(
            services,
            "evaluator_optimizer",
            SpanKind.EVALUATOR,
            evaluator_optimizer_node,
        ),
        "post_eval_loop_decision_node": _wrap_node_with_span(
            services,
            "post_eval_loop_decision",
            SpanKind.EVALUATOR,
            post_eval_loop_decision_node,
        ),
        "answer_regeneration_node": _wrap_node_with_span(
            services, "answer_regeneration", SpanKind.NODE, answer_regeneration_node
        ),
        "final_verifier_node": _wrap_node_with_span(
            services, "final_verifier", SpanKind.GUARDRAIL, final_verifier_node
        ),
        "approval_node": _wrap_node_with_span(
            services, "approval", SpanKind.NODE, approval_node
        ),
        "clarification_node": _wrap_node_with_span(
            services, "clarification", SpanKind.NODE, clarification_node
        ),
        "fail_safe_node": _wrap_node_with_span(
            services, "fail_safe", SpanKind.NODE, fail_safe_node
        ),
        "output_guardrail_node": _wrap_node_with_span(
            services, "output_guardrail", SpanKind.GUARDRAIL, output_guardrail_node
        ),
        "trace_node": shared_nodes["trace_node"],
        "memory_save_node": shared_nodes["memory_save_node"],
        "finalize_node": shared_nodes["finalize_node"],
        "route_after_guardrail": route_after_guardrail,
        "route_loop_decision": route_loop_decision,
        "route_after_retrieval_retry": route_after_retrieval_retry,
        "route_post_eval_loop_decision": route_post_eval_loop_decision,
    }


async def _run_manual_lookup_with_retry(
    state: dict[str, Any],
    services,
    question_override: str | None = None,
) -> dict[str, Any]:
    policy = getattr(services, "agent_loop_policy", AgentLoopPolicy.from_settings())
    question = question_override or state.get("question", "")
    payload = {
        "question": question,
        "device_name": state.get("device_name"),
        "device_model": state.get("device_model"),
        "trace_id": state.get("trace_id"),
    }
    async with trace_span(
        getattr(services, "trace_store", None),
        state.get("trace_id"),
        "tool.manual_lookup",
        SpanKind.TOOL,
        inputs=summarize_span_payload(payload),
        metadata={
            "tool_name": "manual_lookup",
            "max_retries": policy.max_tool_retries,
            "question_preview": _preview(question),
            "device_name": state.get("device_name"),
            "device_model": state.get("device_model"),
        },
    ) as span:
        retry_result = await execute_tool_with_retry(
            getattr(services, "tool_broker", services.tool_registry),
            "manual_lookup",
            payload,
            max_retries=policy.max_tool_retries,
            backoff_ms=policy.retry_backoff_ms,
            trace_store=getattr(services, "trace_store", None),
            trace_id=state.get("trace_id"),
            caller=state.get("intent", "unknown"),
            risk_level=state.get("risk_level", "unknown"),
            run_id=state.get("trace_id"),
        )
        evidence: list[dict[str, Any]] = []
        if (
            retry_result.result is not None
            and retry_result.result.success
            and isinstance(retry_result.result.data, list)
        ):
            evidence = [item for item in retry_result.result.data if isinstance(item, dict)]

        merged_evidence = _dedupe_evidence(state.get("evidence", []) + evidence)
        merged_tool_calls = _dedupe_tool_calls(
            state.get("tool_calls", []) + retry_result.tool_calls
        )
        warnings = _dedupe_strings(state.get("warnings", []))
        if not has_effective_evidence({"evidence": evidence}):
            warnings = _dedupe_strings(
                warnings + ["manual_lookup returned no effective manual evidence"]
            )

        degradation_events = list(
            state.get("degradation_events", [])
            if isinstance(state.get("degradation_events"), list)
            else []
        )
        degradation_events.extend(retry_result.degradation_events)

        result = {
            "evidence": merged_evidence,
            "tool_calls": merged_tool_calls,
            "warnings": warnings,
            "degradation_events": degradation_events,
            "_manual_lookup_attempts": retry_result.attempts,
        }
        metadata = {
            "tool_name": "manual_lookup",
            "max_retries": policy.max_tool_retries,
            "attempts": retry_result.attempts,
            "degraded": retry_result.degraded,
            "evidence_count": len(merged_evidence),
            "placeholder_used": placeholder_used_in_state({"evidence": merged_evidence}),
            "retrieved_pages": _retrieved_pages(merged_evidence),
            "device_name": state.get("device_name"),
            "device_model": state.get("device_model"),
        }
        span.set_metadata(metadata)
        span.set_outputs(
            {
                "evidence_count": len(merged_evidence),
                "tool_call_count": len(merged_tool_calls),
                "degradation_event_count": len(degradation_events),
                "placeholder_used": metadata["placeholder_used"],
                "retrieved_pages": metadata["retrieved_pages"],
            }
        )
        return result


def _build_checkpointer():
    warning = None
    try:
        try:
            from langgraph.checkpoint.memory import InMemorySaver

            return InMemorySaver(), warning
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver

            return MemorySaver(), warning
    except Exception as exc:
        warning = f"LangGraph InMemorySaver unavailable, fallback to no-op mode: {exc}"
        return _NoOpCheckpointer(), warning


def _build_fallback_graph(services):
    class _FallbackGraph:
        async def ainvoke(self, state, config=None):
            del config
            nodes = _build_new_nodes(services)
            policy = getattr(services, "agent_loop_policy", AgentLoopPolicy.from_settings())
            current: dict[str, Any] = {}

            async def apply(node_name: str) -> None:
                nonlocal current
                current |= await nodes[node_name]({**state, **current})

            async def finish() -> dict[str, Any]:
                if "final_verifier_node" in nodes:
                    await apply("final_verifier_node")
                if getattr(settings, "use_output_guardrail", False):
                    await apply("output_guardrail_node")
                await apply("trace_node")
                await apply("memory_save_node")
                await apply("finalize_node")
                return current

            await apply("intake_node")
            await apply("input_guardrail_node")
            if nodes["route_after_guardrail"]({**state, **current}) == "blocked":
                await apply("finalize_node")
                return current

            await apply("memory_load_node")
            await apply("orchestrator_node")
            await apply("worker_executor_node")

            for _ in range(policy.max_loop_steps + 1):
                await apply("loop_decision_node")
                route = nodes["route_loop_decision"]({**state, **current})
                if route == "retry_retrieval":
                    await apply("retrieval_retry_node")
                    retry_route = nodes["route_after_retrieval_retry"]({**state, **current})
                    if retry_route == "evaluate":
                        break
                    continue
                if route in {"approval", "clarification", "fail_safe"}:
                    await apply(f"{route}_node")
                    return await finish()
                break

            for _ in range(policy.max_answer_regenerations + 1):
                await apply("evaluator_optimizer_node")
                await apply("post_eval_loop_decision_node")
                route = nodes["route_post_eval_loop_decision"]({**state, **current})
                if route == "regenerate":
                    await apply("answer_regeneration_node")
                    continue
                if route in {"approval", "clarification", "fail_safe"}:
                    await apply(f"{route}_node")
                break

            return await finish()

    return _FallbackGraph()


async def resume_approval_decision(services, approval_record) -> dict[str, Any]:
    """Resume a saved approval snapshot after a human decision."""
    nodes = _build_new_nodes(services)
    current: dict[str, Any] = dict(approval_record.state_snapshot or {})
    current.update(
        {
            "approval_decision": approval_record.status,
            "approved_approval_id": (
                approval_record.approval_id
                if approval_record.status == "approved"
                else None
            ),
            "approval_scope_hash": approval_record.approval_scope_hash,
            "pending_approval": False,
            "requires_human_approval": False,
            "status": "completed",
            "approval": {
                "approval_id": approval_record.approval_id,
                "status": approval_record.status,
                "reason": approval_record.reason,
                "risk_level": approval_record.risk_level,
                "trace_id": approval_record.trace_id,
                "approval_scope_hash": approval_record.approval_scope_hash,
            },
        }
    )

    async def apply(node_name: str) -> None:
        nonlocal current
        current |= await nodes[node_name](current)

    if approval_record.status == "rejected":
        await apply("fail_safe_node")
        await apply("final_verifier_node")
    else:
        current["answer"] = ""
        await apply("evaluator_optimizer_node")
        await apply("post_eval_loop_decision_node")
        route = nodes["route_post_eval_loop_decision"](current)
        if route in {"approval", "clarification", "fail_safe"}:
            await apply(f"{route}_node")
        else:
            await apply("final_verifier_node")

    if getattr(settings, "use_output_guardrail", False):
        await apply("output_guardrail_node")
    await apply("trace_node")
    await apply("memory_save_node")
    await apply("finalize_node")
    return current


def _loop_decision_update(state: dict[str, Any], services) -> dict[str, Any]:
    # Counts Agent Loop decisions, not executed actions. Actual action counts
    # live in retrieval_retry_count, answer_regeneration_count, and retry attempts.
    decision_count = _current_loop_decision_count(state) + 1
    policy = getattr(services, "agent_loop_policy", AgentLoopPolicy.from_settings())
    controller = getattr(services, "agent_loop_controller", AgentLoopController())
    decision = controller.decide(
        {**state, "loop_decision_count": decision_count},
        policy,
    )
    decision_data = decision.model_dump(mode="json")
    history = _append_loop_history(
        state,
        {
            "decision_count": decision_count,
            "action": decision.action.value,
            "reason": decision.reason,
            "target": decision.target,
            "confidence": decision.confidence,
        },
    )
    return {
        "loop_decision_count": decision_count,
        "loop_history": history,
        "_agent_loop_decision": decision_data,
        "requires_human_approval": (
            True
            if decision.requires_approval
            else bool(state.get("requires_human_approval", False))
        ),
        "approval_reason": (
            decision.reason
            if decision.requires_approval
            else state.get("approval_reason")
        ),
    }


def _current_loop_decision_count(state: dict[str, Any]) -> int:
    value = state.get("loop_decision_count", state.get("loop_step", 0))
    return int(value or 0)


def _append_loop_history(
    state: dict[str, Any],
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    history = state.get("loop_history", [])
    if not isinstance(history, list):
        history = []
    return [entry for entry in history if isinstance(entry, dict)] + [item]


def _decision_action(state: dict[str, Any]) -> str:
    decision = state.get("_agent_loop_decision")
    if isinstance(decision, dict):
        return str(decision.get("action") or AgentLoopAction.FINALIZE.value)
    return AgentLoopAction.FINALIZE.value


def _loop_reason(state: dict[str, Any]) -> str:
    decision = state.get("_agent_loop_decision")
    if isinstance(decision, dict):
        return str(decision.get("reason") or "")
    return ""


def _route_loop_decision_value(state: dict[str, Any]) -> str:
    action = _decision_action(state)
    if action == AgentLoopAction.RETRY_RETRIEVAL.value:
        return "retry_retrieval"
    if action == AgentLoopAction.REQUIRE_APPROVAL.value:
        return "approval"
    if action == AgentLoopAction.ASK_CLARIFICATION.value:
        return "clarification"
    if action == AgentLoopAction.FAIL_SAFE.value:
        return "fail_safe"
    return "evaluate"


def _build_retry_query(state: dict[str, Any]) -> str:
    parts = [
        str(state.get("question") or ""),
        str(state.get("device_name") or ""),
        str(state.get("device_model") or ""),
        "维修手册 检查 参数 故障",
    ]
    return "\n".join(part for part in parts if part.strip())


def _dedupe_evidence(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chunk_id = metadata.get("chunk_id")
        if chunk_id:
            key = ("chunk_id", str(chunk_id))
        else:
            key = (
                "source_page_snippet",
                item.get("source"),
                item.get("page"),
                str(item.get("snippet", "")),
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_tool_calls(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if "attempt" in item:
            deduped.append(item)
            continue
        key = (
            str(item.get("tool_name", "")),
            _stable_json(item.get("input", {})),
            str(item.get("status", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_strings(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)


def _decision_needs_evidence(decision: Any) -> bool:
    workers = getattr(decision, "workers", [])
    if not isinstance(workers, list):
        return False
    return any(worker in {"fault_triage", "sop_guidance"} for worker in workers)


def _tool_call_dict_to_model(item: dict[str, Any]):
    from app.schemas.query import ToolCallItem

    return ToolCallItem(
        tool_name=item.get("tool_name", ""),
        input=item.get("input", {}),
        output=item.get("output"),
        status=item.get("status", "success"),
        duration_ms=item.get("duration_ms"),
    )


def _record_pipeline_meta(services, trace_id: str, state: dict[str, Any]) -> None:
    """Record embedding/reranker/index metadata into the trace."""
    try:
        from app.core.config import settings
        from app.services.manual_vector_indexer import (
            DEFAULT_INDEX_DIR,
        )

        meta_path = DEFAULT_INDEX_DIR / "index_meta.json"
        meta: dict[str, object] = {
            "index_meta_available": meta_path.exists(),
            "index_meta_loaded": False,
        }
        if meta_path.exists():
            stored = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["index_meta_loaded"] = True
            meta["embedding_provider"] = stored.get("provider", "")
            meta["embedding_model"] = stored.get("embedding_model", "")
            meta["embedding_dimensions"] = stored.get("dimensions", 0)

        meta["reranker_enabled"] = settings.reranker_enabled
        if settings.reranker_enabled:
            meta["reranker_model"] = settings.reranker_model
        meta["hyde_enabled"] = settings.hyde_enabled

        meta["evidence_count"] = len(state.get("evidence", []))
        meta["tool_call_count"] = len(state.get("tool_calls", []))
        meta["placeholder_used"] = any(
            isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get("retriever") == "llama-index-placeholder"
            for item in state.get("evidence", [])
        )

        # Attach to trace via the legacy flat dict (backward compat)
        trace = services.trace_store._ensure_trace(trace_id)
        trace["pipeline_meta"] = meta
    except Exception:
        pass


class _NoOpCheckpointer:
    pass
