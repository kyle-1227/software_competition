from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.dependencies import get_trace_store
from app.main import app
from app.schemas.trace import SpanKind, TraceSpan
from app.services.trace_store import TraceStore


def test_get_trace_summary_api(tmp_path) -> None:
    store, trace_id = _store_with_trace(tmp_path)

    with _client_with_trace_store(store) as client:
        response = client.get(f"/api/traces/{trace_id}/summary")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["trace_id"] == trace_id
    assert body["data"]["span_count"] == 1


def test_get_trace_timeline_api(tmp_path) -> None:
    store, trace_id = _store_with_trace(tmp_path)

    with _client_with_trace_store(store) as client:
        response = client.get(f"/api/traces/{trace_id}/timeline")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["format"] == "markdown"
    assert "# Trace Timeline" in body["data"]["timeline"]
    assert "node.orchestrator" in body["data"]["timeline"]


def test_get_trace_raw_api_sanitized(tmp_path) -> None:
    full_script = "print('FULL SCRIPT SHOULD NOT LEAK')\n" * 20
    store, trace_id = _store_with_trace(
        tmp_path,
        span=_span(
            "tool.manual_lookup.attempt",
            SpanKind.TOOL,
            inputs={
                "api_key": "real-api-key",
                "reasoning": "hidden reasoning",
                "script": full_script,
            },
        ),
    )

    with _client_with_trace_store(store) as client:
        response = client.get(f"/api/traces/{trace_id}")

    rendered = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 200
    assert "real-api-key" not in rendered
    assert "hidden reasoning" not in rendered
    assert full_script not in rendered
    assert "script_hash" in rendered


def test_list_traces_api(tmp_path) -> None:
    store, trace_id = _store_with_trace(tmp_path)

    with _client_with_trace_store(store) as client:
        response = client.get("/api/traces")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"][0]["trace_id"] == trace_id
    assert body["data"][0]["span_count"] == 1
    assert body["data"][0]["error_count"] == 0
    assert body["data"][0]["slowest_span_name"] == "node.orchestrator"


def test_get_trace_health_api(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)

    with _client_with_trace_store(store) as client:
        response = client.get("/api/traces/health")

    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["backend"] == "jsonl"
    assert body["data"]["database_url_configured"] is False
    assert "postgresql://" not in json.dumps(body, ensure_ascii=False)


def test_get_trace_spans_tree_and_analytics_api(tmp_path) -> None:
    store, trace_id = _store_with_trace(
        tmp_path,
        span=_span(
            "retriever.vector_search",
            SpanKind.RETRIEVER,
            outputs={"evidence_count": 0, "placeholder_used": True},
        ),
    )

    with _client_with_trace_store(store) as client:
        spans_response = client.get(f"/api/traces/{trace_id}/spans")
        tree_response = client.get(f"/api/traces/{trace_id}/tree")
        analytics_response = client.get(f"/api/traces/{trace_id}/analytics")

    assert spans_response.status_code == 200
    assert spans_response.json()["data"][0]["name"] == "retriever.vector_search"
    assert tree_response.status_code == 200
    assert tree_response.json()["data"]["trace_id"] == trace_id
    assert analytics_response.status_code == 200
    assert analytics_response.json()["data"]["failure_type"] == "retrieval_failure"


def test_get_trace_not_found_api(tmp_path) -> None:
    store = TraceStore(storage_path=tmp_path)

    with _client_with_trace_store(store) as client:
        response = client.get("/api/traces/missing/summary")

    body = response.json()
    assert response.status_code == 404
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"


def _client_with_trace_store(store: TraceStore):
    app.dependency_overrides[get_trace_store] = lambda: store
    client = TestClient(app)

    class _Context:
        def __enter__(self):
            return client.__enter__()

        def __exit__(self, exc_type, exc, tb):
            try:
                return client.__exit__(exc_type, exc, tb)
            finally:
                app.dependency_overrides.pop(get_trace_store, None)

    return _Context()


def _store_with_trace(
    tmp_path,
    *,
    span: TraceSpan | None = None,
) -> tuple[TraceStore, str]:
    store = TraceStore(storage_path=tmp_path)
    trace_id = store.start_trace()
    trace = store.get_trace_session(trace_id)
    assert trace is not None
    trace.root_span.children.append(span or _span("node.orchestrator", SpanKind.NODE))
    store.close_trace(trace_id)
    return store, trace_id


def _span(
    name: str,
    kind: SpanKind,
    *,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TraceSpan:
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return TraceSpan(
        name=name,
        kind=kind,
        start_time=started,
        end_time=started + timedelta(milliseconds=10),
        duration_ms=10,
        inputs=inputs or {},
        outputs=outputs or {},
        metadata=metadata or {"duration_ms": 10},
    )
