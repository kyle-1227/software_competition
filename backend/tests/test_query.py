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
    plan_steps = [item["step"].split(":", 1)[0] for item in data["plan"]]
    assert plan_steps[0] == "intake"
    assert "evaluate" in plan_steps
    assert plan_steps[-1] == "answer"
    assert data["evidence"]
    assert set(data["evidence"][0]["metadata"]) == {
        "chapter",
        "section",
        "block_type",
        "chunk_id",
    }
    assert data["tool_calls"]
    assert data["evaluation"]["confidence"] > 0
    assert data["trace_id"]
    assert data["sop"]
    assert "建议先查" in data["answer"]
    assert "相关页码" in data["answer"]
    assert "证据片段" in data["answer"]
    assert "初步判断" in data["answer"]
    assert "下一步检查" in data["answer"]
    assert "memory" in data
    assert "ai_coding" in data
    assert "llm_usage" in data
    assert "llm_model" in data


def test_query_returns_ai_coding_and_sandbox_result() -> None:
    response = client.post(
        "/api/query",
        json={"question": "生成 SQL 脚本检查诊断记录", "device_name": "摩托车发动机"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ai_coding"]["language"] == "sql"
    assert data["ai_coding"]["sandbox_result"]["allowed"] is True
    assert any(call["tool_name"] == "sandbox_execute" for call in data["tool_calls"])


def test_query_parameter_question_returns_direct_value() -> None:
    response = client.post(
        "/api/query",
        json={"question": "火花塞间隙是多少？", "device_name": "摩托车发动机"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    answer = data["answer"]
    assert "火花塞间隙标准值：0.7～0.9 mm" in answer
    assert "相关页码：P.3" in answer


def test_manual_alias_register_missing_file_uses_error_envelope() -> None:
    response = client.post(
        "/api/manual/register",
        json={"file_path": "missing.pdf", "device_name": "摩托车发动机"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
