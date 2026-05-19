from __future__ import annotations

from typing import Any


class DeepSeekProvider:
    """Adapter around the existing DeepSeek client implementation."""

    name = "deepseek"

    def __init__(self, client: Any) -> None:
        self.client = client

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        return await self.client.generate_text(prompt, context)

    async def generate_json(self, prompt: str, context: dict[str, Any] | None = None):
        return await self.client.generate_json(prompt, context)
