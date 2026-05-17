from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.repository import JsonlTraceRepository
from app.services.tracing.retention import TraceRetentionPolicy
from scripts.cleanup_traces import cleanup_jsonl, cleanup_repository


def test_jsonl_cleanup_dry_run_does_not_modify_file(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    trace_file = tmp_path / "traces.jsonl"
    old = _trace("old", closed_at=datetime.now(timezone.utc) - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n{bad json}\n", encoding="utf-8")
    before = trace_file.read_text(encoding="utf-8")

    stats = cleanup_jsonl(
        repository,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
        archive_path=tmp_path / "archive.jsonl",
        eval_exported=set(),
    )

    assert stats.candidates == 1
    assert stats.would_delete == 1
    assert trace_file.read_text(encoding="utf-8") == before


def test_jsonl_cleanup_apply_rewrites_file_archives_and_keeps_bad_lines(tmp_path) -> None:
    repository = JsonlTraceRepository(tmp_path)
    trace_file = tmp_path / "traces.jsonl"
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    old = _trace(
        "old",
        question=full_question,
        closed_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    new = _trace("new", closed_at=datetime.now(timezone.utc) - timedelta(days=1))
    trace_file.write_text(
        "\n".join([old.model_dump_json(), "{bad json}", new.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    archive = tmp_path / "archive.jsonl"

    stats = cleanup_jsonl(
        repository,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
        archive_path=archive,
        eval_exported=set(),
    )

    remaining = trace_file.read_text(encoding="utf-8")
    archived = archive.read_text(encoding="utf-8")
    assert stats.deleted == 1
    assert "old" not in remaining
    assert "{bad json}" in remaining
    assert "new" in remaining
    assert "old" in archived
    assert full_question not in archived
    assert list(tmp_path.glob("traces.jsonl.bak.*"))


def test_cleanup_repository_respects_batches_and_max_delete(tmp_path) -> None:
    traces = [
        _trace(f"trace-{index}", closed_at=datetime.now(timezone.utc) - timedelta(days=60))
        for index in range(5)
    ]
    repository = _FakeRepository(traces)

    stats = cleanup_repository(
        repository,
        policy=TraceRetentionPolicy(keep_days=30, max_delete=3, batch_size=2, archive_before_delete=False),
        apply=True,
        archive_path=None,
        eval_exported=set(),
    )

    assert stats.candidates == 3
    assert stats.deleted == 3
    assert repository.delete_batches == [2, 1]


def test_cleanup_repository_dry_run_does_not_delete() -> None:
    repository = _FakeRepository([
        _trace("trace", closed_at=datetime.now(timezone.utc) - timedelta(days=60))
    ])

    stats = cleanup_repository(
        repository,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
        archive_path=None,
        eval_exported=set(),
    )

    assert stats.candidates == 1
    assert stats.deleted == 0
    assert repository.deleted == []


class _FakeRepository:
    def __init__(self, traces):
        self.traces = list(traces)
        self.deleted: list[str] = []
        self.delete_batches: list[int] = []

    def list_traces(self, limit=50, session_id=None, status=None):
        del session_id, status
        return self.traces[:limit]

    def delete_traces(self, trace_ids, batch_size=500):
        deleted = 0
        for index in range(0, len(trace_ids), batch_size):
            batch = trace_ids[index : index + batch_size]
            self.delete_batches.append(len(batch))
            self.deleted.extend(batch)
            deleted += len(batch)
        return deleted


def _trace(
    trace_id: str,
    *,
    closed_at: datetime,
    question: str = "q",
) -> Trace:
    return Trace(
        trace_id=trace_id,
        session_id="session",
        question=question,
        status="success",
        created_at=closed_at - timedelta(seconds=1),
        closed_at=closed_at,
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT),
    )
