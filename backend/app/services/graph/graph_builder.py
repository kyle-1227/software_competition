from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.schemas.query import QueryResponse
from app.services.graph.state import HarnessState

logger = logging.getLogger(__name__)


def build_harness_graph(services) -> Any:
    """Build a LangGraph-compatible harness or a safe fallback runner."""
    checkpointer, warning = _build_checkpointer()
    if warning:
        logger.warning(warning)
        services.warnings.append(warning)

    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - optional dependency fallback
        logger.warning("LangGraph unavailable, using fallback runner: %s", exc)
        return _build_fallback_graph(services)

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
            "trace_id": state.get("trace_id") or _trace_placeholder(session_id),
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
            {"step": "理解并规范化用户故障请求", "status": "已完成"},
            {"step": "调用 manual_lookup 检索维修手册证据", "status": "已完成"},
            {"step": "基于证据生成诊断草案", "status": "已完成"},
            {"step": "调用 compliance_check 进行合规检查", "status": "已完成"},
            {"step": "汇总评估结果并记录 trace", "status": "已完成"},
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
        sandbox_result = services.sandbox_executor.execute(
            ai_coding["script"], ai_coding["language"]
        )
        ai_coding["sandbox_result"] = sandbox_result.model_dump(mode="json")
        tool_calls.append(
            {
                "tool_name": "sandbox_execute",
                "input": {
                    "language": ai_coding["language"],
                    "script_hash": _script_hash(ai_coding["script"]),
                    "script_preview": ai_coding["script"][:120],
                },
                "output": sandbox_result.model_dump(mode="json"),
                "status": "success"
                if sandbox_result.allowed and sandbox_result.return_code == 0
                else "failed",
                "duration_ms": sandbox_result.duration_ms,
            }
        )
        return {
            "ai_coding": ai_coding,
            "sandbox_result": sandbox_result.model_dump(mode="json"),
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
        return {
            "sandbox_result": result.model_dump(mode="json"),
            "tool_calls": state.get("tool_calls", []) + [tool_call],
        }

    async def draft_answer_node(state: dict[str, Any]) -> dict[str, Any]:
        context = {
            "question": state["question"],
            "device": state.get("device_model") or state.get("device_name") or "未知设备",
            "memory": state.get("memory", []),
            "evidence": state.get("evidence", []),
            "tool_calls": state.get("tool_calls", []),
            "sandbox_result": state.get("sandbox_result"),
        }
        response = await services.llm_client.generate_text("draft_answer_prompt.md", context=context)
        return {
            "answer": response.text,
            "llm_model": response.model,
            "llm_usage": response.usage,
            "warnings": state.get("warnings", []) + response.warnings,
        }

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


def _needs_ai_coding(question: str) -> bool:
    lowered = question.lower()
    return any(keyword in lowered for keyword in ("脚本", "代码", "script", "code"))


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def _trace_placeholder(session_id: str) -> str:
    return f"trace-{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:12]}"


def _tool_call_dict_to_model(item: dict[str, Any]):
    from app.schemas.query import ToolCallItem

    return ToolCallItem(
        tool_name=item.get("tool_name", ""),
        input=item.get("input", {}),
        output=item.get("output"),
        status=item.get("status", "success"),
        duration_ms=item.get("duration_ms"),
    )


class _NoOpCheckpointer:
    pass
