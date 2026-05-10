from uuid import uuid4

from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, ToolCallItem


class TraceStore:
    """In-memory execution trace store for the Harness MVP.

    The next production step is replacing this process-local store with
    SQLite, PostgreSQL, or Redis so traces survive service restarts.
    """

    def __init__(self) -> None:
        self._traces: dict[str, dict[str, object]] = {}

    def start_trace(self) -> str:
        trace_id = str(uuid4())
        self._traces[trace_id] = {
            "plan": [],
            "tool_calls": [],
            "evidence": [],
            "answer": None,
            "evaluation": None,
        }
        return trace_id

    def record_plan(self, trace_id: str, plan: list[PlanStep]) -> None:
        self._ensure_trace(trace_id)["plan"] = plan

    def record_tool_call(self, trace_id: str, tool_call: ToolCallItem) -> None:
        trace = self._ensure_trace(trace_id)
        tool_calls = trace.setdefault("tool_calls", [])
        assert isinstance(tool_calls, list)
        tool_calls.append(tool_call)

    def record_evidence(self, trace_id: str, evidence: list[EvidenceItem]) -> None:
        self._ensure_trace(trace_id)["evidence"] = evidence

    def record_answer(self, trace_id: str, answer: str) -> None:
        self._ensure_trace(trace_id)["answer"] = answer

    def record_evaluation(
        self, trace_id: str, evaluation: EvaluationResult
    ) -> None:
        self._ensure_trace(trace_id)["evaluation"] = evaluation

    def get_trace(self, trace_id: str) -> dict[str, object]:
        return dict(self._ensure_trace(trace_id))

    def _ensure_trace(self, trace_id: str) -> dict[str, object]:
        if trace_id not in self._traces:
            self._traces[trace_id] = {
                "plan": [],
                "tool_calls": [],
                "evidence": [],
                "answer": None,
                "evaluation": None,
            }
        return self._traces[trace_id]
