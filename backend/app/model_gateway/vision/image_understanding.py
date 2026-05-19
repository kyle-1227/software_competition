from __future__ import annotations

from pydantic import BaseModel, Field


class ImageUnderstandingResult(BaseModel):
    description: str = ""
    evidence: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImageUnderstandingGateway:
    """First-version image understanding facade.

    It returns structured evidence only; final business answers are still
    produced downstream through the evidence ledger and runtime harness.
    """

    async def describe(self, image_bytes: bytes) -> ImageUnderstandingResult:
        return ImageUnderstandingResult(
            description=f"image_bytes={len(image_bytes)}",
            evidence=[],
            warnings=["image understanding provider is not configured"],
        )
