from __future__ import annotations

import json
from io import StringIO

from app.services.tracing.trace_import import (
    ImportOptions,
    import_traces,
    iter_import_spans,
    normalize_trace_payload_for_import,
)
from scripts.import_traces_to_postgres import main


def test_import_dry_run_does_not_write_and_outputs_json_stats(tmp_path, capsys) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload()) + "\n", encoding="utf-8")

    code = main(["--file", str(trace_file)])

    captured = capsys.readouterr()
    stats = json.loads(captured.out.splitlines()[-1])
    assert code == 0
    assert stats["dry_run"] is True
    assert stats["lines_seen"] == 1
    assert stats["valid_traces"] == 1
    assert stats["would_import"] == 1
    assert stats["imported"] == 0


def test_import_apply_writes_trace_and_non_root_spans(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload(include_root_span_id=True)) + "\n", encoding="utf-8")
    repository = _FakeRepository()

    stats = import_traces(
        ImportOptions(file=trace_file, database_url="postgresql://example/db", apply=True),
        repository_factory=lambda url: repository,
    )

    assert stats.imported == 1
    assert repository.initialize_calls == 1
    assert repository.saved_traces == ["trace-import"]
    assert [span.name for span in repository.saved_spans] == ["node.parent", "tool.child"]
    assert "harness" not in [span.name for span in repository.saved_spans]
    assert repository.closed_traces == ["trace-import"]


def test_import_stats_for_empty_bad_filtered_and_limited_lines(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    rows = [
        "",
        "{bad json",
        json.dumps(_trace_payload(trace_id="skip-me")),
        json.dumps(_trace_payload(trace_id="keep-me")),
        json.dumps(_trace_payload(trace_id="after-limit")),
    ]
    trace_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    stats = import_traces(
        ImportOptions(file=trace_file, trace_id="keep-me", limit=1),
        stderr=StringIO(),
    )

    assert stats.lines_seen == 5
    assert stats.valid_traces == 3
    assert stats.would_import == 1
    assert stats.imported == 0
    assert stats.failed == 1
    assert stats.skipped == 3


def test_import_legacy_ok_status_is_valid(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    payload = _trace_payload()
    payload["status"] = "ok"
    trace_file.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    stats = import_traces(ImportOptions(file=trace_file), stderr=StringIO())

    assert stats.valid_traces == 1
    assert stats.would_import == 1
    assert stats.failed == 0


def test_import_skip_existing_skips_all_writes(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload()) + "\n", encoding="utf-8")
    repository = _FakeRepository(existing_trace_ids={"trace-import"})

    stats = import_traces(
        ImportOptions(
            file=trace_file,
            database_url="postgresql://example/db",
            apply=True,
            skip_existing=True,
        ),
        repository_factory=lambda url: repository,
    )

    assert stats.skipped == 1
    assert stats.imported == 0
    assert repository.saved_traces == []
    assert repository.saved_spans == []
    assert repository.closed_traces == []


def test_import_without_skip_existing_uses_upsert_writes(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload()) + "\n", encoding="utf-8")
    repository = _FakeRepository(existing_trace_ids={"trace-import"})

    stats = import_traces(
        ImportOptions(file=trace_file, database_url="postgresql://example/db", apply=True),
        repository_factory=lambda url: repository,
    )

    assert stats.imported == 1
    assert repository.saved_traces == ["trace-import"]


def test_import_normalization_fills_deterministic_non_root_span_ids() -> None:
    payload = _trace_payload(include_span_ids=False)

    first = normalize_trace_payload_for_import(payload)
    second = normalize_trace_payload_for_import(payload)

    first_child = first["root_span"]["children"][0]
    second_child = second["root_span"]["children"][0]
    nested = first_child["children"][0]
    assert first["root_span"].get("span_id") is None
    assert first_child["span_id"] == second_child["span_id"]
    assert first_child["trace_id"] == "trace-import"
    assert first_child["parent_span_id"] == "trace-import:root"
    assert nested["parent_span_id"] == first_child["span_id"]


def test_iter_import_spans_excludes_root_span(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload()) + "\n", encoding="utf-8")
    repository = _FakeRepository()

    import_traces(
        ImportOptions(file=trace_file, database_url="postgresql://example/db", apply=True),
        repository_factory=lambda url: repository,
    )

    trace = repository.trace_objects[0]
    assert [span.name for span in iter_import_spans(trace)] == ["node.parent", "tool.child"]


def test_import_error_output_is_redacted(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    payload = _trace_payload()
    payload["question"] = "api_key=real-api-key password=real-password help"
    trace_file.write_text("{bad json with token=raw-token}\n" + json.dumps(payload) + "\n", encoding="utf-8")
    stderr = StringIO()
    repository = _FakeRepository(fail_on_save=True)

    stats = import_traces(
        ImportOptions(
            file=trace_file,
            database_url="postgresql://example/db",
            apply=True,
            verbose=True,
        ),
        repository_factory=lambda url: repository,
        stderr=stderr,
    )

    rendered = stderr.getvalue()
    assert stats.failed == 2
    assert "raw-token" not in rendered
    assert "real-api-key" not in rendered
    assert "real-password" not in rendered
    assert "full script should not leak" not in rendered
    assert "full answer should not leak" not in rendered
    assert "question_hash" in rendered


def test_import_missing_database_url_in_apply_mode_returns_failure(tmp_path) -> None:
    trace_file = tmp_path / "traces.jsonl"
    trace_file.write_text(json.dumps(_trace_payload()) + "\n", encoding="utf-8")
    stderr = StringIO()

    stats = import_traces(ImportOptions(file=trace_file, apply=True), stderr=stderr)

    assert stats.failed == 1
    assert "database_url_required" in stderr.getvalue()


def _trace_payload(trace_id: str = "trace-import", include_span_ids: bool = True, include_root_span_id: bool = False):
    parent = {
        "name": "node.parent",
        "kind": "node",
        "start_time": "2026-01-01T00:00:01Z",
        "children": [
            {
                "name": "tool.child",
                "kind": "tool",
                "start_time": "2026-01-01T00:00:02Z",
                "inputs": {"script": "full script should not leak"},
                "outputs": {"answer": "full answer should not leak"},
            }
        ],
    }
    if include_span_ids:
        parent["span_id"] = f"{trace_id}-parent"
        parent["children"][0]["span_id"] = f"{trace_id}-child"
    root = {
        "name": "harness",
        "kind": "agent",
        "children": [parent],
    }
    if include_root_span_id:
        root["span_id"] = f"{trace_id}-root"
    return {
        "trace_id": trace_id,
        "session_id": "session-import",
        "question": "why did it fail?",
        "status": "success",
        "root_span": root,
    }


class _FakeRepository:
    def __init__(self, existing_trace_ids=None, fail_on_save: bool = False) -> None:
        self.existing_trace_ids = set(existing_trace_ids or set())
        self.fail_on_save = fail_on_save
        self.initialize_calls = 0
        self.saved_traces: list[str] = []
        self.saved_spans = []
        self.closed_traces: list[str] = []
        self.trace_objects = []

    def initialize(self):
        self.initialize_calls += 1

    def get_trace(self, trace_id):
        return object() if trace_id in self.existing_trace_ids else None

    def save_trace(self, trace):
        if self.fail_on_save:
            raise RuntimeError("api_key=repo-secret token=repo-token")
        self.saved_traces.append(trace.trace_id)
        self.trace_objects.append(trace)

    def save_span(self, trace_id, span):
        if self.fail_on_save:
            raise RuntimeError("password=span-password")
        self.saved_spans.append(span)

    def close_trace(self, trace):
        if self.fail_on_save:
            raise RuntimeError("secret=close-secret")
        self.closed_traces.append(trace.trace_id)
