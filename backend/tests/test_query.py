from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_query_returns_harness_trace_fields() -> None:
    response = client.post(
        "/api/query",
        json={"question": "发动机无法启动怎么办", "device_name": "摩托车发动机"},
    )

    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert body["success"] is True
    assert data["answer"]
    assert data["plan"]
    assert data["evidence"]
    assert data["tool_calls"]
    assert data["evaluation"]["confidence"] > 0
    assert data["trace_id"]
    assert data["sop"]


def test_manual_alias_register_missing_file_uses_error_envelope() -> None:
    response = client.post(
        "/api/manual/register",
        json={"file_path": "missing.pdf", "device_name": "摩托车发动机"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
