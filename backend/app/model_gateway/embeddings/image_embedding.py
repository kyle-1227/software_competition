from __future__ import annotations

import hashlib


class ImageEmbeddingGateway:
    """Deterministic first-version image embedding facade."""

    dimensions = 16

    async def embed(self, image_bytes: bytes) -> list[float]:
        digest = hashlib.sha256(image_bytes).digest()
        return [round(byte / 255.0, 6) for byte in digest[: self.dimensions]]
