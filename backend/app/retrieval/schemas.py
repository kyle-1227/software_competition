from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalQuery(BaseModel):
    query: str
    device_model: str | None = None
    top_k: int = 5


class RetrievalResult(BaseModel):
    evidence_id: str
    source: str
    source_id: str | None = None
    page: int | None = None
    snippet: str
    score: float | None = None
    retrieval_method: str = "keyword"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_evidence_item(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "source_id": self.source_id,
            "page": self.page,
            "snippet": self.snippet,
            "score": self.score,
            "metadata": self.metadata,
        }
