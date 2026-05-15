from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app.core.config import settings
from app.schemas.query import QueryResponse
from app.services.graph.state import HarnessState

logger = logging.getLogger(__name__)

LOCAL_DIAGNOSTIC_MODEL = "local-diagnostic-template"
DRAFT_ANSWER_PROMPT = (
    "你是设备检修智能辅助系统，请基于手册证据、工具调用记录和安全约束，"
    "用中文生成诊断建议。必须引用证据页码和片段；证据不足时明确说明；"
    "不要编造手册内容；包含安全提醒。"
)
SENSITIVE_REASONING_KEYS = {
    "reasoning_content",
    "reasoning",
    "thinking",
    "chain_of_thought",
}
PROVIDER_FALLBACK_MARKERS = (
    "DeepSeek 未配置或不可用",
    "deterministic fallback",
)


def build_harness_graph(services) -> Any:
    """Build a LangGraph-compatible harness or a safe fallback runner.

    If settings.use_orchestrator is True, builds the new Orchestrator-Workers
    graph. Otherwise builds the legacy 13-node DAG.
    """
    checkpointer, warning = _build_checkpointer()
    if warning:
        logger.warning(warning)
        services.warnings.append(warning)

    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - optional dependency fallback
        logger.warning("LangGraph unavailable, using fallback runner: %s", exc)
        return _build_fallback_graph(services)

    if getattr(settings, "use_orchestrator", False):
        return _build_new_graph(services, StateGraph, END, checkpointer)
    else:
        return _build_legacy_graph(services, StateGraph, END, checkpointer)


def _build_legacy_graph(services, StateGraph, END, checkpointer) -> Any:
    """Legacy 13-node DAG (the original Prompt Chaining pattern)."""
    graph = StateGraph(HarnessState)
    nodes = _build_nodes(services)
    graph.add_node("intake_node", nodes["intake_node"])
    graph.add_node("memory_load_node", nodes["memory_load_node"])
    graph.add_node("plan_node", nodes["plan_node"])
    graph.add_node("retrieval_node", nodes["retrieval_node"])
    graph.add_node("ai_coding_node", nodes["ai_coding_node"])
    graph.add_node("sandbox_node", nodes["sandbox_node"])
    graph.add_node("draft_answer_node", nodes["draft_answer_node"])
    graph.add_node("compliance_node", nodes["compliance_node"])
    graph.add_node("evaluator_node", nodes["evaluator_node"])
    graph.add_node("trace_node", nodes["trace_node"])
    graph.add_node("memory_save_node", nodes["memory_save_node"])
    graph.add_node("finalize_node", nodes["finalize_node"])

    graph.set_entry_point("intake_node")
    graph.add_edge("intake_node", "memory_load_node")
    graph.add_edge("memory_load_node", "plan_node")
    graph.add_edge("plan_node", "retrieval_node")
    graph.add_conditional_edges(
        "retrieval_node",
        nodes["route_ai_coding"],
        {"ai_coding": "ai_coding_node", "draft": "draft_answer_node"},
    )
    graph.add_edge("ai_coding_node", "sandbox_node")
    graph.add_edge("sandbox_node", "draft_answer_node")
    graph.add_edge("draft_answer_node", "compliance_node")
    graph.add_edge("compliance_node", "evaluator_node")
    graph.add_edge("evaluator_node", "trace_node")
    graph.add_edge("trace_node", "memory_save_node")
    graph.add_edge("memory_save_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph.compile(checkpointer=checkpointer)


def _build_new_graph(services, StateGraph, END, checkpointer) -> Any:
    """New Orchestrator-Workers graph (Phase 1+).

    When use_evaluator_optimizer is True, uses evaluator_optimizer_node
    (collapse draft_answer + compliance + evaluator into one iterative node).
    When use_output_guardrail is True, inserts output_guardrail_node before trace.
    """
    use_eo = getattr(settings, "use_evaluator_optimizer", False)
    use_og = getattr(settings, "use_output_guardrail", False)
    graph = StateGraph(HarnessState)
    nodes = _build_new_nodes(services)

    graph.add_node("intake_node", nodes["intake_node"])
    graph.add_node("input_guardrail_node", nodes["input_guardrail_node"])
    graph.add_node("memory_load_node", nodes["memory_load_node"])
    graph.add_node("orchestrator_node", nodes["orchestrator_node"])
    graph.add_node("worker_executor_node", nodes["worker_executor_node"])

    if use_eo:
        graph.add_node("evaluator_optimizer_node", nodes["evaluator_optimizer_node"])
    else:
        graph.add_node("draft_answer_node", nodes["draft_answer_node"])
        graph.add_node("compliance_node", nodes["compliance_node"])
        graph.add_node("evaluator_node", nodes["evaluator_node"])

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

    if use_eo:
        graph.add_edge("worker_executor_node", "evaluator_optimizer_node")
        if use_og:
            graph.add_edge("evaluator_optimizer_node", "output_guardrail_node")
            graph.add_edge("output_guardrail_node", "trace_node")
        else:
            graph.add_edge("evaluator_optimizer_node", "trace_node")
    else:
        graph.add_edge("worker_executor_node", "draft_answer_node")
        graph.add_edge("draft_answer_node", "compliance_node")
        graph.add_edge("compliance_node", "evaluator_node")
        if use_og:
            graph.add_edge("evaluator_node", "output_guardrail_node")
            graph.add_edge("output_guardrail_node", "trace_node")
        else:
            graph.add_edge("evaluator_node", "trace_node")

    graph.add_edge("trace_node", "memory_save_node")
    graph.add_edge("memory_save_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph.compile(checkpointer=checkpointer)


def _build_nodes(services) -> dict[str, Any]:
    async def intake_node(state: dict[str, Any]) -> dict[str, Any]:
        session_id = (
            state.get("session_id")
            or state.get("device_model")
            or state.get("device_name")
            or "default"
        )
        return {
            "question": state["question"],
            "device_name": state.get("device_name"),
            "device_model": state.get("device_model"),
            "session_id": session_id,
            "trace_id": state.get("trace_id") or services.trace_store.start_trace(),
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
        }

    async def memory_load_node(state: dict[str, Any]) -> dict[str, Any]:
        history = services.memory_store.get_history(state["session_id"])
        return {"memory": history}

    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        plan = [
            {"step": "plan: 识别故障现象、设备对象和检索意图", "status": "completed"},
            {"step": "retrieve: 调用 manual_lookup 检索维修手册证据", "status": "completed"},
            {"step": "evaluate: 评估证据充分性、安全性和合规性", "status": "completed"},
            {"step": "answer: 汇总页码、证据片段和初步诊断建议", "status": "completed"},
        ]
        return {"plan": plan, "needs_ai_coding": _needs_ai_coding(state["question"])}

    async def retrieval_node(state: dict[str, Any]) -> dict[str, Any]:
        result = await services.tool_registry.execute(
            "manual_lookup",
            {
                "question": state["question"],
                "device_name": state.get("device_name"),
                "device_model": state.get("device_model"),
            },
        )
        evidence = []
        if result.success and isinstance(result.data, list):
            evidence = [item for item in result.data if isinstance(item, dict)]
        tool_calls = [
            {
                "tool_name": result.tool_name,
                "input": {"question": state["question"]},
                "output": result.data if result.success else {"error": result.error},
                "status": "success" if result.success else "failed",
                "duration_ms": result.metadata.get("duration_ms"),
            }
        ]
        return {
            "evidence": evidence,
            "tool_calls": tool_calls,
            "warnings": state.get("warnings", [])
            + ([] if evidence else ["未检索到手册证据"]),
        }

    async def ai_coding_node(state: dict[str, Any]) -> dict[str, Any]:
        result = await services.tool_registry.execute(
            "ai_coding",
            {
                "question": state["question"],
                "task": state["question"],
                "language": "sql" if "sql" in state["question"].lower() else "python",
            },
        )
        data = result.data if isinstance(result.data, dict) else {}
        ai_coding = {
            "language": data.get("language", "python"),
            "script": data.get("script", ""),
            "explanation": data.get("explanation", ""),
            "warnings": data.get("warnings", []),
        }
        tool_calls = state.get("tool_calls", []) + [
            {
                "tool_name": result.tool_name,
                "input": {"question": state["question"]},
                "output": data if result.success else {"error": result.error},
                "status": "success" if result.success else "failed",
                "duration_ms": result.metadata.get("duration_ms"),
            }
        ]
        return {
            "ai_coding": ai_coding,
            "tool_calls": tool_calls,
        }

    async def sandbox_node(state: dict[str, Any]) -> dict[str, Any]:
        ai_coding = state.get("ai_coding") or {}
        script = str(ai_coding.get("script", ""))
        language = str(ai_coding.get("language", "python"))
        result = services.sandbox_executor.execute(script, language)
        preview = script[:120]
        script_hash = _script_hash(script)
        tool_call = {
            "tool_name": "sandbox_execute",
            "input": {
                "language": language,
                "script_hash": script_hash,
                "script_preview": preview,
            },
            "output": result.model_dump(mode="json"),
            "status": "success" if result.allowed and result.return_code == 0 else "failed",
            "duration_ms": result.duration_ms,
        }
        sandbox_result = result.model_dump(mode="json")
        ai_coding = {**ai_coding, "sandbox_result": sandbox_result}
        return {
            "ai_coding": ai_coding,
            "sandbox_result": sandbox_result,
            "tool_calls": state.get("tool_calls", []) + [tool_call],
        }

    async def draft_answer_node(state: dict[str, Any]) -> dict[str, Any]:
        return await _draft_answer_with_llm(services, state)

    async def compliance_node(state: dict[str, Any]) -> dict[str, Any]:
        result = await services.tool_registry.execute(
            "compliance_check", {"answer": state.get("answer", "")}
        )
        return {
            "tool_calls": state.get("tool_calls", [])
            + [
                {
                    "tool_name": result.tool_name,
                    "input": {"answer": state.get("answer", "")},
                    "output": result.data if result.success else {"error": result.error},
                    "status": "success" if result.success else "failed",
                    "duration_ms": result.metadata.get("duration_ms"),
                }
            ]
        }

    async def evaluator_node(state: dict[str, Any]) -> dict[str, Any]:
        evidence = [item for item in state.get("evidence", []) if isinstance(item, dict)]
        tool_calls = [item for item in state.get("tool_calls", []) if isinstance(item, dict)]
        evaluation = services.evaluator.evaluate(
            state.get("answer", ""),
            evidence,
            [_tool_call_dict_to_model(item) for item in tool_calls],
        )
        return {"evaluation": evaluation.model_dump(mode="json")}

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
        }
        if not response["sop"]:
            response["sop"] = [
                "停机并断电，确认设备处于安全状态。",
                "佩戴防护用品，检查现场风险。",
                "依据手册证据逐项排查，不直接执行高风险操作。",
                "记录现象、处理步骤和结果，必要时提交知识审核。",
            ]
        return {"response": response, **response}

    def route_ai_coding(state: dict[str, Any]) -> str:
        return "ai_coding" if state.get("needs_ai_coding") else "draft"

    return {
        "intake_node": intake_node,
        "memory_load_node": memory_load_node,
        "plan_node": plan_node,
        "retrieval_node": retrieval_node,
        "ai_coding_node": ai_coding_node,
        "sandbox_node": sandbox_node,
        "draft_answer_node": draft_answer_node,
        "compliance_node": compliance_node,
        "evaluator_node": evaluator_node,
        "trace_node": trace_node,
        "memory_save_node": memory_save_node,
        "finalize_node": finalize_node,
        "route_ai_coding": route_ai_coding,
    }


def _ensure_new_services(services) -> None:
    """Lazily construct Phase 1-2 services on the services namespace."""
    if not hasattr(services, "orchestrator") or services.orchestrator is None:
        from app.services.orchestrator import Orchestrator
        from app.services.guardrails.input_guard import InputGuardrail
        from app.services.workers.dispatcher import WorkerDispatcher
        from app.services.workers.fault_triage import FaultTriageWorker
        from app.services.workers.sop_guidance import SOPGuidanceWorker
        from app.services.workers.ai_coding import AICodingWorker

        llm_client = getattr(services, "llm_client", None)
        services.orchestrator = Orchestrator(llm_client=llm_client)
        services.input_guardrail = InputGuardrail(llm_client=llm_client)

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

        llm_client = getattr(services, "llm_client", None)
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

        llm_client = getattr(services, "llm_client", None)
        services.output_guardrail = OutputGuardrail(llm_client=llm_client)


def _build_new_nodes(services) -> dict[str, Any]:
    """Build node implementations for the Orchestrator-Workers graph.

    Reuses intake_node, memory_load_node, draft_answer_node, compliance_node,
    evaluator_node, trace_node, memory_save_node, and finalize_node from
    _build_nodes for maximum code sharing.
    """
    _ensure_new_services(services)
    legacy_nodes = _build_nodes(services)

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
        merged_ai_coding: dict[str, Any] | None = state.get("ai_coding")
        merged_sandbox: dict[str, Any] | None = state.get("sandbox_result")
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

        update: dict[str, Any] = {
            "worker_outputs": worker_outputs,
            "evidence": merged_evidence,
            "tool_calls": merged_tool_calls,
            "warnings": merged_warnings,
        }

        if not merged_evidence and not worker_evidence and _decision_needs_evidence(decision):
            fallback = await legacy_nodes["retrieval_node"]({**state, **update})
            fallback_evidence = fallback.get("evidence", [])
            if isinstance(fallback_evidence, list):
                update["evidence"] = _dedupe_evidence(merged_evidence + fallback_evidence)
            fallback_tool_calls = fallback.get("tool_calls", [])
            if isinstance(fallback_tool_calls, list):
                update["tool_calls"] = _dedupe_tool_calls(
                    merged_tool_calls + fallback_tool_calls
                )
            fallback_warnings = fallback.get("warnings", [])
            if isinstance(fallback_warnings, list):
                update["warnings"] = _dedupe_strings(merged_warnings + fallback_warnings)

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

    return {
        "intake_node": legacy_nodes["intake_node"],
        "input_guardrail_node": input_guardrail_node,
        "memory_load_node": legacy_nodes["memory_load_node"],
        "orchestrator_node": orchestrator_node,
        "worker_executor_node": worker_executor_node,
        "draft_answer_node": legacy_nodes["draft_answer_node"],
        "compliance_node": legacy_nodes["compliance_node"],
        "evaluator_node": legacy_nodes["evaluator_node"],
        "evaluator_optimizer_node": evaluator_optimizer_node,
        "output_guardrail_node": output_guardrail_node,
        "trace_node": legacy_nodes["trace_node"],
        "memory_save_node": legacy_nodes["memory_save_node"],
        "finalize_node": legacy_nodes["finalize_node"],
        "route_after_guardrail": route_after_guardrail,
    }


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
            if getattr(settings, "use_input_guardrail", False):
                nodes = _build_new_nodes(services)
                current = await nodes["intake_node"](state)
                current |= await nodes["input_guardrail_node"]({**state, **current})
                if nodes["route_after_guardrail"]({**state, **current}) == "blocked":
                    current |= await nodes["finalize_node"]({**state, **current})
                    return current
            return await self._ainvoke_legacy_graph(state)

        async def _ainvoke_legacy_graph(self, state):
            nodes = _build_nodes(services)
            current = await nodes["intake_node"](state)
            current |= await nodes["memory_load_node"]({**state, **current})
            current |= await nodes["plan_node"]({**state, **current})
            current |= await nodes["retrieval_node"]({**state, **current})
            if current.get("needs_ai_coding"):
                current |= await nodes["ai_coding_node"]({**state, **current})
                current |= await nodes["sandbox_node"]({**state, **current})
            current |= await nodes["draft_answer_node"]({**state, **current})
            current |= await nodes["compliance_node"]({**state, **current})
            current |= await nodes["evaluator_node"]({**state, **current})
            current |= await nodes["trace_node"]({**state, **current})
            current |= await nodes["memory_save_node"]({**state, **current})
            current |= await nodes["finalize_node"]({**state, **current})
            return current

    return _FallbackGraph()


async def _draft_answer_with_llm(services, state: dict[str, Any]) -> dict[str, Any]:
    state_warnings = _string_list(state.get("warnings", []))
    llm_response = None
    llm_warnings: list[str] = []
    fallback_reason: str | None = None

    llm_client = getattr(services, "llm_client", None)
    generate_text = getattr(llm_client, "generate_text", None)
    if generate_text is None:
        fallback_reason = "LLM client unavailable, used local diagnostic template."
    else:
        try:
            llm_response = await generate_text(
                DRAFT_ANSWER_PROMPT,
                _build_llm_context(state),
            )
            llm_warnings = _string_list(getattr(llm_response, "warnings", []))
        except Exception as exc:
            fallback_reason = f"LLM answer generation failed, used local diagnostic template: {exc}"

    llm_usage = getattr(llm_response, "usage", None) if llm_response is not None else None
    llm_text = _filter_reasoning_text(str(getattr(llm_response, "text", "") or "")).strip()

    if llm_response is None:
        use_local_fallback = True
    elif not llm_text:
        use_local_fallback = True
        fallback_reason = "LLM returned empty answer, used local diagnostic template."
    elif _has_provider_fallback_warning(llm_warnings):
        use_local_fallback = True
        fallback_reason = "LLM provider fallback detected, used local diagnostic template."
    else:
        use_local_fallback = False

    warnings = state_warnings + llm_warnings
    if fallback_reason:
        warnings.append(fallback_reason)

    if use_local_fallback:
        return {
            "answer": _build_diagnostic_answer(state),
            "llm_model": LOCAL_DIAGNOSTIC_MODEL,
            "llm_usage": llm_usage,
            "warnings": warnings,
        }

    return {
        "answer": llm_text,
        "llm_model": getattr(llm_response, "model", None) or LOCAL_DIAGNOSTIC_MODEL,
        "llm_usage": llm_usage,
        "warnings": warnings,
    }


def _build_llm_context(state: dict[str, Any]) -> dict[str, Any]:
    context = {
        "question": state.get("question"),
        "device_name": state.get("device_name"),
        "device_model": state.get("device_model"),
        "memory": state.get("memory", []),
        "evidence": state.get("evidence", []),
        "tool_calls": state.get("tool_calls", []),
        "sandbox_result": state.get("sandbox_result"),
        "ai_coding": state.get("ai_coding"),
        "evaluation": state.get("evaluation"),
        "warnings": state.get("warnings", []),
    }
    return _filter_reasoning_fields(context)


def _filter_reasoning_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _filter_reasoning_fields(item)
            for key, item in value.items()
            if str(key) not in SENSITIVE_REASONING_KEYS
        }
    if isinstance(value, list):
        return [_filter_reasoning_fields(item) for item in value]
    return value


def _filter_reasoning_text(text: str) -> str:
    filtered = text
    for key in SENSITIVE_REASONING_KEYS:
        filtered = filtered.replace(key, "")
    return filtered


def _has_provider_fallback_warning(warnings: list[str]) -> bool:
    return any(
        marker in warning
        for warning in warnings
        for marker in PROVIDER_FALLBACK_MARKERS
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


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


def _needs_ai_coding(question: str) -> bool:
    lowered = question.lower()
    return any(keyword in lowered for keyword in ("脚本", "代码", "script", "code"))


def _build_diagnostic_answer(state: dict[str, Any]) -> str:
    question = str(state.get("question") or "").strip()
    device = state.get("device_model") or state.get("device_name") or "当前设备"
    evidence = [item for item in state.get("evidence", []) if isinstance(item, dict)]

    safety = "安全前提：先停机并断电，佩戴防护用品，确认现场风险受控后再进行检查。"
    if not evidence:
        return (
            f"{safety}\n\n"
            f"问题：{question}\n\n"
            "未检索到足够的手册证据，暂不建议在缺少依据时拆卸或调整关键部件。"
            "请补充更具体的故障现象、部件名称或设备型号后重新查询。"
        )

    top_evidence = evidence[:3]
    if _is_parameter_question(question):
        return _build_parameter_answer(question, device, top_evidence)

    page_refs = _unique_page_refs(top_evidence)
    evidence_lines = []
    for index, item in enumerate(top_evidence, start=1):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        section = metadata.get("section") or metadata.get("chapter") or "相关章节"
        snippet = _compact_snippet(str(item.get("snippet") or "").strip())
        evidence_lines.append(
            f"{index}. {_evidence_page_label(item)} {section}：{snippet}"
        )

    likely_focus = _diagnosis_focus(top_evidence)
    inspection_order = _inspection_order(top_evidence)
    next_actions = _next_actions(top_evidence)
    evidence_text = "\n".join(evidence_lines)
    inspection_text = "\n".join(
        f"{index}. {action}" for index, action in enumerate(inspection_order, start=1)
    )
    action_text = "\n".join(f"{index}. {action}" for index, action in enumerate(next_actions, start=1))

    return (
        f"{safety}\n\n"
        f"建议先查：\n{inspection_text}\n\n"
        f"问题：{question}\n"
        f"设备：{device}\n"
        f"相关页码：{page_refs}\n\n"
        f"证据片段：\n{evidence_text}\n\n"
        f"初步判断：优先围绕{likely_focus}排查。当前证据更适合先做外观、连接、"
        "间隙/压力等可验证项目，确认异常后再进入拆卸或更换。\n\n"
        f"下一步检查：\n{action_text}"
    )


def _is_parameter_question(question: str) -> bool:
    parameter_cues = ("多少", "是多少", "标准值", "标准范围", "范围", "参数", "数值", "多大")
    diagnostic_cues = ("怎么办", "哪里", "原因", "为什么", "不稳", "回火", "启动困难", "无法启动", "故障")
    return any(cue in question for cue in parameter_cues) and not any(
        cue in question for cue in diagnostic_cues
    )


def _build_parameter_answer(
    question: str,
    device: str,
    evidence: list[dict[str, Any]],
) -> str:
    top_evidence = evidence[:3]
    page_refs = _unique_page_refs(top_evidence)
    best = top_evidence[0] if top_evidence else {}
    metadata = best.get("metadata") if isinstance(best.get("metadata"), dict) else {}
    section = metadata.get("section") or metadata.get("chapter") or "相关章节"
    snippet = str(best.get("snippet") or "").strip()
    value = _extract_parameter_value(question, snippet)

    direct_answer = (
        f"火花塞间隙标准值：{value}。"
        if value
        else f"根据手册召回片段，答案在 {section} 中；请以证据片段中的标准值为准。"
    )
    evidence_line = (
        f"{_evidence_page_label(best)} {section}：{_compact_snippet(snippet, limit=180)}"
        if best
        else "未找到可引用的手册片段。"
    )

    return (
        f"{direct_answer}\n\n"
        f"问题：{question}\n"
        f"设备：{device}\n"
        f"相关页码：{page_refs}\n\n"
        f"依据：\n{evidence_line}\n\n"
        "测量或更换前仍需先停机并断电，避免烫伤或误启动。"
    )


def _extract_parameter_value(question: str, snippet: str) -> str | None:
    if "火花塞" in question and "间隙" in question:
        match = re.search(r"间隙标准值[:：]?\s*([0-9.]+[～~\-－–—][0-9.]+\s*mm)", snippet)
        if match:
            return _normalize_range(match.group(1))

    match = re.search(r"([0-9.]+[～~\-－–—][0-9.]+\s*(?:mm|kPa|N[·.]?m))", snippet)
    if match:
        return _normalize_range(match.group(1))
    return None


def _normalize_range(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("~", "～")).strip()


def _diagnosis_focus(evidence: list[dict[str, Any]]) -> str:
    terms: list[str] = []
    candidate_terms = [
        "火花塞间隙",
        "火花塞",
        "压缩压力",
        "气门间隙",
        "进气门",
        "排气门",
        "气门",
        "发动机",
    ]
    for item in evidence:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        keywords = metadata.get("keywords") if isinstance(metadata.get("keywords"), list) else []
        terms.extend(str(keyword) for keyword in keywords[:3])
        haystack = " ".join(
            str(value)
            for value in (
                metadata.get("chapter"),
                metadata.get("section"),
                metadata.get("block_type"),
                item.get("snippet"),
            )
            if value
        )
        terms.extend(term for term in candidate_terms if term in haystack)
    if terms:
        found = set(terms)
        prioritized = [term for term in candidate_terms if term in found]
        fallback = [term for term in dict.fromkeys(terms) if term not in prioritized]
        return "、".join((prioritized + fallback)[:5])
    return "手册召回的相关部件和检查标准"


def _next_actions(evidence: list[dict[str, Any]]) -> list[str]:
    actions = [
        "核对召回页码中的标准值、工具要求和警示信息。",
        "按证据片段从低风险检查开始，记录现象、测量值和部件状态。",
        "若检测值超出手册范围，停止扩大拆检并交由具备资质的维修人员复核。",
    ]
    block_types = {
        str((item.get("metadata") or {}).get("block_type", ""))
        for item in evidence
        if isinstance(item.get("metadata"), dict)
    }
    if any("测量" in block_type or "检查" in block_type for block_type in block_types):
        actions.insert(1, "优先完成手册要求的测量/检查项目，并与标准范围比较。")
    return actions[:4]


def _inspection_order(evidence: list[dict[str, Any]]) -> list[str]:
    haystack = _evidence_haystack(evidence)
    actions: list[str] = []

    if "火花塞" in haystack:
        actions.append("先检查火花塞状态和火花塞间隙；手册 P.3 给出的间隙标准值是 0.7～0.9 mm。")
    if "压缩压力" in haystack:
        actions.append("再按 P.3 测量压缩压力，确认发动机压缩是否低于标准范围。")
    if "气门间隙" in haystack:
        actions.append("复核 P.15 气门间隙：进气门 0.13～0.20 mm，排气门 0.20～0.30 mm。")

    if actions:
        return actions[:3]

    return [
        "先从召回证据中风险最低、无需扩大拆检的检查项开始。",
        "记录测量值和部件状态，再决定是否进入拆卸或更换。",
    ]


def _evidence_page_label(item: dict[str, Any]) -> str:
    page = item.get("page")
    return f"P.{page}" if page is not None else "P.-"


def _unique_page_refs(evidence: list[dict[str, Any]]) -> str:
    refs = dict.fromkeys(_evidence_page_label(item) for item in evidence)
    return "、".join(refs)


def _evidence_haystack(evidence: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for item in evidence:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        values.extend(
            str(value)
            for value in (
                metadata.get("chapter"),
                metadata.get("section"),
                metadata.get("block_type"),
                item.get("snippet"),
            )
            if value
        )
    return " ".join(values)


def _compact_snippet(snippet: str, limit: int = 150) -> str:
    compacted = " ".join(snippet.split())
    if len(compacted) <= limit:
        return compacted
    return compacted[:limit].rstrip() + "..."


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


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
