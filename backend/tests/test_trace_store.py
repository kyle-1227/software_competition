from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, SandboxResult
from app.schemas.trace import TraceStatus
from app.services.trace_store import TraceStore


def test_trace_store_records_full_flow() -> None:
    store = TraceStore()
    trace_id = store.start_trace(session_id="session-1", question="question")
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


def test_trace_store_start_trace_uses_session_and_question(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace(session_id="session-1", question="Why fail?")

    trace = store.get_trace_session(trace_id)

    assert trace is not None
    assert trace.session_id == "session-1"
    assert trace.question == "Why fail?"
    assert trace.normalized_question == "why fail?"


def test_trace_store_close_trace_calculates_total_duration(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace(session_id="session-1", question="q")

    trace = store.close_trace(trace_id)

    assert trace is not None
    assert trace.closed_at is not None
    assert trace.total_duration_ms is not None
    assert trace.status == TraceStatus.SUCCESS


def test_trace_store_records_repository_failure_health(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path, repository=_FailingRepository())
    trace_id = store.start_trace(session_id="session-1", question="q")

    store.close_trace(trace_id)
    health = store.health_status()

    assert health["healthy"] is False
    assert health["degraded"] is True
    assert "repository unavailable" in health["last_error"]


class _FailingRepository:
    def initialize(self):
        return None

    def save_trace(self, trace):
        raise RuntimeError("repository unavailable")

    def save_span(self, trace_id, span):
        raise RuntimeError("repository unavailable")

    def close_trace(self, trace):
        raise RuntimeError("repository unavailable")

    def get_trace(self, trace_id):
        return None

    def list_traces(self, limit=50, session_id=None, status=None):
        return []

    def list_trace_summaries(self, limit=50, session_id=None, status=None):
        return []

    def list_spans(self, trace_id):
        return []

    def healthcheck(self):
        return False

    def health_status(self):
        from app.services.tracing.repository import RepositoryHealth

        return RepositoryHealth(
            backend="failing",
            configured_backend="failing",
            healthy=False,
            degraded=True,
            last_error="repository unavailable",
        )
