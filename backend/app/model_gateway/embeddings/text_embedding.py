from __future__ import annotations

import hashlib


class TextEmbeddingGateway:
    """Deterministic first-version text embedding facade."""

    dimensions = 16

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [round(byte / 255.0, 6) for byte in digest[: self.dimensions]]
