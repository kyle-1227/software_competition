from __future__ import annotations

from typing import Any


class OpenAICompatibleProvider:
    """Placeholder for future OpenAI-compatible providers."""

    name = "openai_compatible"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    async def generate_text(self, prompt: str, context: dict[str, Any] | None = None):
        if self.client is None:
            raise RuntimeError("OpenAI-compatible provider is not configured")
        return await self.client.generate_text(prompt, context)

    async def generate_json(self, prompt: str, context: dict[str, Any] | None = None):
        if self.client is None:
            raise RuntimeError("OpenAI-compatible provider is not configured")
        return await self.client.generate_json(prompt, context)
