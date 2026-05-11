from app.schemas.query import EvidenceItem

try:  # Optional integration point; placeholder mode works without LlamaIndex.
    from llama_index.core import Document, VectorStoreIndex
except Exception:  # pragma: no cover - depends on optional local environment.
    Document = None  # type: ignore[assignment]
    VectorStoreIndex = None  # type: ignore[assignment]


class Retriever:
    def __init__(self) -> None:
        self._index = None

    async def search_evidence(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        # Next step: build VectorStoreIndex from data/processed manual chunks and
        # persist it under data/indexes. This placeholder keeps the public JSON
        # contract stable while LlamaIndex is not configured.
        device_hint = device_model or "未知型号"
        return [
            EvidenceItem(
                source=f"manual::{device_hint}",
                page=None,
                snippet=(
                    "当前为 LlamaIndex 兼容占位证据；后续将替换为真实的手册分块、"
                    "页码和向量检索得分。"
                ),
                score=0.42,
                metadata={
                    "retriever": "llama-index-placeholder",
                    "question": question,
                },
            )
        ]

    async def search(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        return await self.search_evidence(question, device_model)
