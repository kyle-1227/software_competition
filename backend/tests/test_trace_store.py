from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, SandboxResult
from app.services.trace_store import TraceStore


def test_trace_store_records_full_flow() -> None:
    store = TraceStore()
    trace_id = store.start_trace()
    store.record_plan(trace_id, [PlanStep(step="plan", status="done")])
    store.record_evidence(trace_id, [EvidenceItem(source="manual", snippet="snippet")])
    store.record_answer(trace_id, "answer")
    store.record_evaluation(
        trace_id,
        EvaluationResult(
            is_safe=True, is_compliant=True, confidence=1.0, issues=[]
        ),
    )
    store.record_memory(trace_id, [{"trace_id": "old"}])
    store.record_sandbox_result(
        trace_id,
        SandboxResult(language="python", allowed=True, return_code=0, stdout="ok"),
    )

    trace = store.get_trace(trace_id)
    assert trace["plan"]
    assert trace["evidence"]
    assert trace["answer"] == "answer"
    assert trace["evaluation"] is not None
    assert trace["memory"] == [{"trace_id": "old"}]
    assert trace["sandbox_result"] is not None
