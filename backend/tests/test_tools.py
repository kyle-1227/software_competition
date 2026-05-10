import pytest

from app.services.tool_registry import ToolRegistry


@pytest.mark.anyio
async def test_tool_registry_executes_default_tools() -> None:
    registry = ToolRegistry()

    manual_result = await registry.execute(
        "manual_lookup", {"question": "发动机无法启动", "device_model": "CG125"}
    )
    assert manual_result.success is True
    assert isinstance(manual_result.data, list)

    coding_result = await registry.execute("ai_coding", {"task": "生成诊断脚本"})
    assert coding_result.success is True
    assert isinstance(coding_result.data, dict)
    assert coding_result.data["language"] == "python"

    compliance_result = await registry.execute(
        "compliance_check", {"answer": "先停机断电，佩戴防护用品，确认风险。"}
    )
    assert compliance_result.success is True
    assert isinstance(compliance_result.data, dict)
    assert compliance_result.data["is_compliant"] is True
