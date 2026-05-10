from pathlib import Path
from typing import Any

from app.schemas.query import (
    EvidenceItem,
    PlanStep,
    QueryRequest,
    QueryResponse,
    SandboxResult,
    ToolCallItem,
)
from app.services.evaluator import Evaluator
from app.services.memory_store import MemoryStore
from app.services.sandbox import SandboxExecutor
from app.services.tool_registry import ToolRegistry, ToolResult
from app.services.trace_store import TraceStore

try:  # Optional LangChain integration point.
    from langchain_core.prompts import PromptTemplate
except Exception:  # pragma: no cover - depends on optional local environment.
    PromptTemplate = None  # type: ignore[assignment]


class AgentHarness:
    """Plan -> Tool -> Draft Answer -> Evaluator -> Trace Harness.

    This MVP uses deterministic local behavior. Real LLM calls can be added by
    replacing _draft_answer with a LangChain chain using the prompt templates.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        trace_store: TraceStore | None = None,
        memory_store: MemoryStore | None = None,
        sandbox_executor: SandboxExecutor | None = None,
        evaluator: Evaluator | None = None,
    ) -> None:
        self.tool_registry = tool_registry or ToolRegistry()
        self.trace_store = trace_store or TraceStore()
        self.memory_store = memory_store or MemoryStore()
        self.sandbox_executor = sandbox_executor or SandboxExecutor()
        self.evaluator = evaluator or Evaluator()

    async def answer(self, payload: QueryRequest) -> QueryResponse:
        trace_id = self.trace_store.start_trace()
        session_id = self._session_id(payload)
        memory = self.memory_store.get_history(session_id)
        self.trace_store.record_memory(trace_id, memory)
        plan = self._build_plan(payload)
        self.trace_store.record_plan(trace_id, plan)

        tool_calls: list[ToolCallItem] = []
        manual_result = await self.tool_registry.execute(
            "manual_lookup",
            {
                "question": payload.question,
                "device_name": payload.device_name,
                "device_model": payload.device_model,
            },
        )
        tool_calls.append(self._to_tool_call(manual_result, payload.question))

        evidence = self._extract_evidence(manual_result)
        self.trace_store.record_evidence(trace_id, evidence)

        ai_result = None
        ai_coding: dict[str, Any] | None = None
        sandbox_result: SandboxResult | None = None
        if self._needs_ai_coding(payload.question):
            ai_result = await self.tool_registry.execute(
                "ai_coding",
                {
                    "question": payload.question,
                    "task": payload.question,
                    "language": self._infer_script_language(payload.question),
                },
            )
            tool_calls.append(self._to_tool_call(ai_result, payload.question))
            if isinstance(ai_result.data, dict):
                ai_coding = dict(ai_result.data)
                sandbox_result = self.sandbox_executor.execute(
                    str(ai_result.data.get("script", "")),
                    str(ai_result.data.get("language", "python")),
                )
                ai_coding["sandbox_result"] = sandbox_result.model_dump(mode="json")
                tool_calls.append(
                    ToolCallItem(
                        tool_name="sandbox_execute",
                        input={
                            "language": sandbox_result.language,
                            "script": ai_result.data.get("script", ""),
                        },
                        output=sandbox_result.model_dump(mode="json"),
                        status="success"
                        if sandbox_result.allowed and sandbox_result.return_code == 0
                        else "failed",
                        duration_ms=sandbox_result.duration_ms,
                    )
                )
                self.trace_store.record_sandbox_result(trace_id, sandbox_result)

        answer = self._draft_answer(payload, evidence, [manual_result, ai_result])
        self.trace_store.record_answer(trace_id, answer)

        compliance_result = await self.tool_registry.execute(
            "compliance_check", {"answer": answer}
        )
        tool_calls.append(self._to_tool_call(compliance_result, payload.question))

        for tool_call in tool_calls:
            self.trace_store.record_tool_call(trace_id, tool_call)

        evaluation = self.evaluator.evaluate(answer, evidence, tool_calls)
        self.trace_store.record_evaluation(trace_id, evaluation)
        self.memory_store.add_trace(
            session_id,
            {
                "trace_id": trace_id,
                "question": payload.question,
                "answer": answer,
                "evaluation": evaluation.model_dump(mode="json"),
            },
        )

        return QueryResponse(
            answer=answer,
            plan=plan,
            evidence=evidence,
            tool_calls=tool_calls,
            evaluation=evaluation,
            trace_id=trace_id,
            sop=self._build_sop(),
            memory=memory,
            ai_coding=ai_coding,
        )

    def _build_plan(self, payload: QueryRequest) -> list[PlanStep]:
        del payload
        return [
            PlanStep(step="理解并规范化用户故障请求", status="已完成"),
            PlanStep(step="调用 manual_lookup 检索维修手册证据", status="已完成"),
            PlanStep(step="基于证据生成诊断草案", status="已完成"),
            PlanStep(step="调用 compliance_check 进行合规检查", status="已完成"),
            PlanStep(step="汇总评估结果并记录 trace", status="已完成"),
        ]

    def _draft_answer(
        self,
        payload: QueryRequest,
        evidence: list[EvidenceItem],
        tool_results: list[ToolResult | None],
    ) -> str:
        del tool_results
        template = self._load_prompt_template("draft_answer_prompt.md")
        evidence_text = "；".join(item.snippet for item in evidence) or "暂无证据"
        device = payload.device_model or payload.device_name or "未知设备"
        # The template is loaded to make future LangChain integration explicit;
        # deterministic text keeps tests stable until an LLM provider is configured.
        del template
        return (
            f"针对 {device} 的问题“{payload.question}”，建议先停机并断电，"
            "佩戴必要防护用品，确认现场风险后再排查。"
            f"当前检索到的证据为：{evidence_text}。"
            "请优先核对手册页码和设备型号，再执行拆检或更换部件。"
        )

    def _load_prompt_template(self, filename: str) -> str:
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / filename
        if not prompt_path.exists():
            return ""
        return prompt_path.read_text(encoding="utf-8")

    def _extract_evidence(self, result: ToolResult) -> list[EvidenceItem]:
        if not result.success or not isinstance(result.data, list):
            return []
        return [EvidenceItem(**item) for item in result.data if isinstance(item, dict)]

    def _to_tool_call(self, result: ToolResult, question: str) -> ToolCallItem:
        duration_ms = result.metadata.get("duration_ms")
        return ToolCallItem(
            tool_name=result.tool_name,
            input={"question": question},
            output=result.data if result.success else {"error": result.error},
            status="success" if result.success else "failed",
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
        )

    def _needs_ai_coding(self, question: str) -> bool:
        return any(keyword in question.lower() for keyword in ("脚本", "代码", "script", "code"))

    def _infer_script_language(self, question: str) -> str:
        lowered = question.lower()
        if "sql" in lowered or "数据库" in question:
            return "sql"
        if "shell" in lowered or "powershell" in lowered or "命令" in question:
            return "shell"
        return "python"

    def _session_id(self, payload: QueryRequest) -> str:
        return payload.device_model or payload.device_name or "default"

    def _build_sop(self) -> list[str]:
        return [
            "停机并断电，确认设备处于安全状态。",
            "佩戴防护用品，检查现场风险。",
            "依据手册证据逐项排查，不直接执行高风险操作。",
            "记录现象、处理步骤和结果，必要时提交知识审核。",
        ]
