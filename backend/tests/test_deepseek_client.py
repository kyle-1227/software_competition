import pytest

from app.services.llm.deepseek_client import DeepSeekLLMClient


def test_deepseek_client_defaults_to_fallback_without_api_key() -> None:
    client = DeepSeekLLMClient(api_key=None)
    response = _run(client.generate_text("prompt", {}))

    assert "fallback" in response.text.lower()
    assert response.usage is None


@pytest.mark.anyio
async def test_deepseek_client_generate_json_fallback_filters_reasoning() -> None:
    client = DeepSeekLLMClient(api_key=None)
    response = await client.generate_json("prompt", {"reasoning_content": "x"})

    assert "reasoning_content" not in response.text
    assert response.usage is None


def _run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
