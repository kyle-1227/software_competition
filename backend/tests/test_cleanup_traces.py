from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from app.schemas.trace import SpanKind, Trace, TraceSpan
from app.services.tracing.retention import (
    TraceRetentionPolicy,
    cleanup_jsonl_traces,
    cleanup_postgres_traces,
)
from scripts.cleanup_traces import main


def test_cleanup_jsonl_dry_run_does_not_modify_file(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    old = _trace("old", closed_at=datetime.now(timezone.utc) - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n{bad json}\n", encoding="utf-8")
    before = trace_file.read_text(encoding="utf-8")

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
    )

    assert stats.candidates == 1
    assert stats.would_delete == 1
    assert stats.deleted == 0
    assert trace_file.read_text(encoding="utf-8") == before


def test_cleanup_jsonl_apply_rewrites_and_creates_backup(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    old = _trace("old", question=full_question, closed_at=datetime.now(timezone.utc) - timedelta(days=60))
    new = _trace("new", closed_at=datetime.now(timezone.utc) - timedelta(days=1))
    trace_file.write_text(
        "\n".join([old.model_dump_json(), "{bad json}", new.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    archive = tmp_path / "archive.jsonl"

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
        archive_path=archive,
    )

    remaining = trace_file.read_text(encoding="utf-8")
    archived = archive.read_text(encoding="utf-8")
    assert stats.deleted == 1
    assert stats.failed >= 1  # bad JSON line
    assert "old" not in remaining
    assert "{bad json}" in remaining
    assert "new" in remaining
    assert "old" in archived
    assert full_question not in archived
    assert list(tmp_path.glob("traces.jsonl.bak.*"))


def test_cleanup_jsonl_running_and_open_traces_preserved(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    running = _trace("running", closed_at=now - timedelta(days=100), status="running")
    open_trace = _trace("open", closed_at=None, status="success")
    old = _trace("old-success", closed_at=now - timedelta(days=60))
    trace_file.write_text(
        "\n".join([running.model_dump_json(), open_trace.model_dump_json(), old.model_dump_json()]) + "\n",
        encoding="utf-8",
    )

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
    )

    remaining = trace_file.read_text(encoding="utf-8")
    assert stats.deleted == 1
    assert "running" in remaining
    assert "open" in remaining
    assert "old-success" not in remaining


def test_cleanup_jsonl_eval_exported_uses_eval_dataset(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    eval_dataset = tmp_path / "cases.jsonl"
    now = datetime.now(timezone.utc)
    evaled = _trace("eval-trace", closed_at=now - timedelta(days=120))
    trace_file.write_text(evaled.model_dump_json() + "\n", encoding="utf-8")
    eval_dataset.write_text(json.dumps({"case_id": "c", "trace_id": "eval-trace"}) + "\n", encoding="utf-8")

    stats_no_dataset = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30, keep_eval_exported_days=180),
        apply=False,
    )
    stats_with_dataset = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30, keep_eval_exported_days=180),
        eval_dataset_path=eval_dataset,
        apply=False,
    )

    assert stats_no_dataset.candidates == 1
    assert stats_with_dataset.candidates == 0


def test_cleanup_jsonl_respects_max_delete(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    traces = [_trace(f"trace-{i}", closed_at=now - timedelta(days=60)) for i in range(5)]
    trace_file.write_text("\n".join(t.model_dump_json() for t in traces) + "\n", encoding="utf-8")

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30, max_delete=2),
        apply=True,
    )

    assert stats.deleted == 2


def test_cleanup_jsonl_no_archive_before_delete(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    old = _trace("old", closed_at=now - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n", encoding="utf-8")

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30, archive_before_delete=False),
        apply=True,
    )

    assert stats.archived == 0


def test_cleanup_jsonl_missing_file_succeeds(tmp_path) -> None:
    missing = tmp_path / "missing_traces.jsonl"

    stats = cleanup_jsonl_traces(
        missing,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
    )

    assert stats.candidates == 0


def test_cleanup_jsonl_apply_zero_candidates_no_files_created(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    recent = _trace("recent", closed_at=now - timedelta(days=5))
    trace_file.write_text(recent.model_dump_json() + "\n", encoding="utf-8")

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
    )

    assert stats.deleted == 0
    assert stats.archived == 0
    assert not list(tmp_path.glob("traces.jsonl.bak.*"))
    assert not list(tmp_path.glob("traces.jsonl.tmp*"))


def test_cleanup_cli_bad_line_not_fatal(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    old = _trace("old", closed_at=datetime.now(timezone.utc) - timedelta(days=60))
    trace_file.write_text("{bad json}\n" + old.model_dump_json() + "\n", encoding="utf-8")

    code = main(["--backend", "jsonl", "--jsonl-path", str(trace_file), "--apply"])

    assert code == 0


def test_cleanup_cli_postgres_not_implemented(tmp_path) -> None:
    code = main(["--backend", "postgres", "--database-url", ""])

    assert code == 1


def test_cleanup_cli_invalid_policy_returns_1(tmp_path) -> None:
    code = main(["--backend", "jsonl", "--keep-days", "-1"])

    assert code == 1


def test_cleanup_jsonl_archive_failure_does_not_rewrite(tmp_path, monkeypatch) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    old = _trace("old", closed_at=now - timedelta(days=60))
    recent = _trace("recent", closed_at=now - timedelta(days=5))
    trace_file.write_text(
        "\n".join([old.model_dump_json(), recent.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    original = trace_file.read_text(encoding="utf-8")

    def _fail(*args, **kwargs):
        raise OSError("archive write failed")

    monkeypatch.setattr("app.services.tracing.retention._write_archive", _fail)

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
    )

    assert stats.fatal is True
    assert stats.deleted == 0
    assert trace_file.read_text(encoding="utf-8") == original


def test_cleanup_jsonl_dry_run_reports_expected_archive_path(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    old = _trace("old", closed_at=now - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n", encoding="utf-8")
    archive = tmp_path / "archive.jsonl"

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
        archive_path=archive,
    )

    assert stats.candidates == 1
    assert stats.would_archive == 1
    assert stats.archive_path == str(archive)
    assert stats.backup_path is None
    assert not archive.exists()
    assert not list(tmp_path.glob("traces.jsonl.bak.*"))
    assert not list(tmp_path.glob("traces.jsonl.tmp*"))


def test_cleanup_jsonl_dry_run_no_archive_path_when_archive_disabled(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    old = _trace("old", closed_at=now - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n", encoding="utf-8")

    stats = cleanup_jsonl_traces(
        trace_file,
        policy=TraceRetentionPolicy(keep_days=30, archive_before_delete=False),
        apply=False,
    )

    assert stats.candidates == 1
    assert stats.would_archive == 0
    assert stats.archive_path is None


# -- PostgreSQL cleanup tests --


def test_cleanup_postgres_dry_run_does_not_delete() -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("old", closed_at=now - timedelta(days=60)),
    ])

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
    )

    assert stats.candidates == 1
    assert stats.would_delete == 1
    assert repo.deleted_batches == []


def test_cleanup_postgres_apply_deletes_in_batches() -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace(f"trace-{i}", closed_at=now - timedelta(days=60))
        for i in range(5)
    ])

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30, max_delete=5, batch_size=2),
        apply=True,
    )

    assert stats.deleted == 5
    assert repo.deleted_batches == [2, 2, 1]


def test_cleanup_postgres_archive_before_delete(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    full_question = "FULL QUESTION SHOULD NOT LEAK " * 20
    repo = _FakePostgresRepository([
        _trace("old", question=full_question, closed_at=now - timedelta(days=60)),
    ])
    archive = tmp_path / "archive.jsonl"

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
        archive_path=archive,
    )

    assert stats.archived == 1
    assert stats.deleted == 1
    archived_content = archive.read_text(encoding="utf-8")
    assert full_question not in archived_content


def test_cleanup_postgres_archive_failure_prevents_delete(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("old", closed_at=now - timedelta(days=60)),
    ])

    def _fail(*args, **kwargs):
        raise OSError("archive write failed")

    monkeypatch.setattr("app.services.tracing.retention._write_archive", _fail)

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
    )

    assert stats.fatal is True
    assert stats.deleted == 0


def test_cleanup_postgres_eval_exported_retention(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("eval-trace", closed_at=now - timedelta(days=120)),
    ])
    eval_dataset = tmp_path / "cases.jsonl"
    eval_dataset.write_text(json.dumps({"case_id": "c", "trace_id": "eval-trace"}) + "\n", encoding="utf-8")

    stats_no_eval = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30, keep_eval_exported_days=180),
        apply=False,
    )
    assert stats_no_eval.candidates == 1

    stats_with_eval = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30, keep_eval_exported_days=180),
        eval_dataset_path=eval_dataset,
        apply=False,
    )
    assert stats_with_eval.candidates == 0


def test_cleanup_postgres_repository_list_failure_is_fatal() -> None:
    repo = _FakePostgresRepository([])
    repo.list_should_fail = True

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=False,
    )

    assert stats.fatal is True
    assert stats.failed >= 1


# -- CLI tests for postgres --


def test_cleanup_cli_postgres_missing_db_url_fails(tmp_path) -> None:
    code = main(["--backend", "postgres", "--database-url", ""])

    assert code == 1


def test_cleanup_cli_postgres_fake_repository(monkeypatch, tmp_path) -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("old", closed_at=now - timedelta(days=60)),
    ])

    def _fake_repo(*args, **kwargs):
        return repo

    monkeypatch.setattr("scripts.cleanup_traces.PostgreSQLTraceRepository", _fake_repo)

    code = main([
        "--backend", "postgres",
        "--database-url", "postgresql://fake",
        "--apply",
    ])

    assert code == 0
    assert repo.deleted_batches


def test_cleanup_cli_auto_postgres_when_db_url(monkeypatch, tmp_path) -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("old", closed_at=now - timedelta(days=60)),
    ])

    def _fake_repo(*args, **kwargs):
        return repo

    monkeypatch.setattr("scripts.cleanup_traces.PostgreSQLTraceRepository", _fake_repo)

    code = main([
        "--backend", "auto",
        "--database-url", "postgresql://fake",
        "--apply",
    ])

    assert code == 0
    assert repo.deleted_batches


def test_cleanup_cli_auto_jsonl_without_db_url(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    now = datetime.now(timezone.utc)
    old = _trace("old", closed_at=now - timedelta(days=60))
    trace_file.write_text(old.model_dump_json() + "\n", encoding="utf-8")

    code = main([
        "--backend", "auto",
        "--database-url", "",
        "--jsonl-path", str(trace_file),
    ])

    assert code == 0


def test_cleanup_postgres_missing_trace_for_archive_prevents_delete(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    repo = _FakePostgresRepository([
        _trace("old", closed_at=now - timedelta(days=60)),
    ])
    repo.missing_get_trace_ids = {"old"}

    stats = cleanup_postgres_traces(
        repo,
        policy=TraceRetentionPolicy(keep_days=30),
        apply=True,
        archive_path=tmp_path / "archive.jsonl",
    )

    assert stats.fatal is True
    assert stats.deleted == 0
    assert repo.deleted_batches == []


class _FakePostgresRepository:
    def __init__(self, traces):
        self.traces = {t.trace_id: t for t in traces}
        self.deleted_batches: list[int] = []
        self.archived: list[str] = []
        self.list_should_fail = False
        self.missing_get_trace_ids: set[str] = set()

    def initialize(self) -> None:
        pass

    def list_trace_cleanup_rows(self, *, limit=5000):
        if self.list_should_fail:
            raise RuntimeError("list failed")
        now = datetime.now(timezone.utc)
        rows = []
        for t in self.traces.values():
            rows.append({
                "trace_id": t.trace_id,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "closed_at": t.closed_at,
                "degraded": False,
                "fallback_used": False,
            })
        return rows[:limit]

    def get_trace(self, trace_id):
        if trace_id in self.missing_get_trace_ids:
            return None
        return self.traces.get(trace_id)

    def delete_traces(self, trace_ids, batch_size=500):
        deleted = 0
        for i in range(0, len(trace_ids), batch_size):
            batch = trace_ids[i:i + batch_size]
            self.deleted_batches.append(len(batch))
            deleted += len(batch)
        return deleted


def _trace(
    trace_id: str,
    *,
    closed_at: datetime | None = None,
    status: str = "success",
    question: str = "q",
) -> Trace:
    now = datetime.now(timezone.utc)
    return Trace(
        trace_id=trace_id,
        session_id="session",
        question=question,
        status=status,
        created_at=(closed_at or now) - timedelta(seconds=1),
        closed_at=closed_at,
        root_span=TraceSpan(name="harness", kind=SpanKind.AGENT),
    )
