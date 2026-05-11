import pytest

from app.schemas.query import EvidenceItem
from app.services.retriever import Retriever


@pytest.mark.anyio
async def test_retriever_returns_structured_evidence() -> None:
    retriever = Retriever()
    evidence = await retriever.search("发动机无法启动", "摩托车发动机")

    assert evidence
    assert isinstance(evidence[0], EvidenceItem)
    assert evidence[0].source.startswith("manual::")
    assert evidence[0].score is not None
    assert evidence[0].metadata["retriever"] == "llama-index-placeholder"
