from datetime import datetime, timezone

from app.schemas.query import EvidenceItem, EvaluationResult, PlanStep, SandboxResult
from app.schemas.trace import SpanKind, SpanStatus, TraceSpan, TraceStatus
from app.services.trace_store import TraceStore
from app.services.tracing.repository import RepositoryHealth


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
    assert health["ever_degraded"] is True
    assert health["last_error_at"] is not None
    assert "repository unavailable" in health["last_error"]


def test_trace_store_repository_health_recovers_after_success(tmp_path) -> None:
    repository = _ControllableRepository(fail_operations={"save_span"})
    store = TraceStore(storage_path=tmp_path, repository=repository)
    trace_id = store.start_trace(session_id="session-1", question="q")

    store.add_span(trace_id, _span("node.first"))
    failed_health = store.health_status()

    assert failed_health["healthy"] is False
    assert failed_health["degraded"] is True
    assert failed_health["ever_degraded"] is True
    assert failed_health["last_error_at"] is not None

    repository.fail_operations.clear()
    store.add_span(trace_id, _span("node.second"))
    recovered_health = store.health_status()

    assert recovered_health["healthy"] is True
    assert recovered_health["degraded"] is False
    assert recovered_health["ever_degraded"] is True
    assert recovered_health["last_error"] is None
    assert recovered_health["last_success_at"] is not None


def test_trace_store_healthcheck_failure_recovers_on_later_healthcheck(tmp_path) -> None:
    repository = _ControllableRepository(fail_operations={"healthcheck"})
    store = TraceStore(storage_path=tmp_path, repository=repository)

    failed_health = store.health_status()

    assert failed_health["healthy"] is False
    assert failed_health["degraded"] is True
    assert failed_health["ever_degraded"] is True

    repository.fail_operations.clear()
    recovered_health = store.health_status()

    assert recovered_health["healthy"] is True
    assert recovered_health["degraded"] is False
    assert recovered_health["ever_degraded"] is True
    assert recovered_health["last_success_at"] is not None


def test_trace_store_records_synthetic_repository_failure_span(tmp_path) -> None:
    repository = _ControllableRepository(fail_operations={"save_span"})
    store = TraceStore(storage_path=tmp_path, repository=repository)
    trace_id = store.start_trace(session_id="session-1", question="q")

    store.add_span(trace_id, _span("node.original"))

    trace = store.get_trace_session(trace_id)
    assert trace is not None
    synthetic = [
        span
        for span in trace.root_span.children
        if span.name == "trace.repository.save_span"
    ]

    assert len(synthetic) == 1
    span = synthetic[0]
    assert span.status == SpanStatus.ERROR
    assert span.metadata["trace_repository_failure"] is True
    assert span.metadata["synthetic"] is True
    assert span.metadata["system_span"] is True
    assert span.metadata["affects_user_answer"] is False
    assert span.metadata["persisted"] is False
    assert span.degraded is True
    assert repository.save_span_calls == 1


def test_trace_store_flushes_unpersisted_system_spans_on_close(tmp_path) -> None:
    repository = _ControllableRepository(fail_operations={"save_span"})
    store = TraceStore(storage_path=tmp_path, repository=repository)
    trace_id = store.start_trace(session_id="session-1", question="q")

    store.add_span(trace_id, _span("node.original"))
    repository.fail_operations.clear()
    trace = store.close_trace(trace_id)

    assert trace is not None
    synthetic = next(
        span
        for span in trace.root_span.children
        if span.name == "trace.repository.save_span"
    )
    assert synthetic.metadata["persisted"] is True
    assert repository.save_span_calls >= 2


def test_trace_store_close_trace_failure_keeps_closed_trace_with_system_span(tmp_path) -> None:
    repository = _ControllableRepository(fail_operations={"close_trace"})
    store = TraceStore(storage_path=tmp_path, repository=repository)
    trace_id = store.start_trace(session_id="session-1", question="q")

    trace = store.close_trace(trace_id)
    loaded = store.get_trace_tree(trace_id)

    assert trace is not None
    assert loaded is trace
    assert any(
        span.name == "trace.repository.close_trace"
        and span.status == SpanStatus.ERROR
        and span.metadata.get("trace_repository_failure") is True
        for span in loaded.root_span.children
    )
    assert repository.close_trace_calls == 1


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
            ever_degraded=True,
            last_error="repository unavailable",
            last_error_at=datetime.now(timezone.utc),
        )


class _ControllableRepository:
    def __init__(self, fail_operations=None):
        self.fail_operations = set(fail_operations or set())
        self.degraded = False
        self.ever_degraded = False
        self.last_error = None
        self.last_error_at = None
        self.last_success_at = None
        self.save_span_calls = 0
        self.close_trace_calls = 0

    def initialize(self):
        self._record_success()

    def save_trace(self, trace):
        if "save_trace" in self.fail_operations:
            self._raise("save_trace unavailable")
        self._record_success()

    def save_span(self, trace_id, span):
        self.save_span_calls += 1
        if "save_span" in self.fail_operations:
            self._raise("save_span unavailable")
        self._record_success()

    def close_trace(self, trace):
        self.close_trace_calls += 1
        if "close_trace" in self.fail_operations:
            self._raise("close_trace unavailable")
        self._record_success()

    def get_trace(self, trace_id):
        return None

    def list_traces(self, limit=50, session_id=None, status=None):
        return []

    def list_trace_summaries(self, limit=50, session_id=None, status=None):
        return []

    def list_spans(self, trace_id):
        return []

    def healthcheck(self):
        if self.fail_operations:
            self._record_error(RuntimeError("healthcheck unavailable"))
            return False
        self._record_success()
        return True

    def health_status(self):
        healthy = self.healthcheck()
        return RepositoryHealth(
            backend="fake",
            configured_backend="fake",
            healthy=healthy,
            degraded=self.degraded or not healthy,
            ever_degraded=self.ever_degraded,
            last_error=self.last_error,
            last_error_at=self.last_error_at,
            last_success_at=self.last_success_at,
        )

    def _raise(self, message):
        exc = RuntimeError(message)
        self._record_error(exc)
        raise exc

    def _record_success(self):
        self.degraded = False
        self.last_error = None
        self.last_success_at = datetime.now(timezone.utc)

    def _record_error(self, exc):
        self.degraded = True
        self.ever_degraded = True
        self.last_error = str(exc)
        self.last_error_at = datetime.now(timezone.utc)


def _span(name: str) -> TraceSpan:
    return TraceSpan(name=name, kind=SpanKind.NODE)
