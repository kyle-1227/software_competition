from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.query import EvidenceItem
from app.services.retriever import Retriever


@pytest.mark.anyio
async def test_retriever_converts_vector_nodes_to_evidence() -> None:
    retriever = Retriever(
        vector_retriever=_FakeVectorRetriever(
            [
                _FakeNodeWithScore(
                    text="1.2 检查火花塞\n间隙标准值：0.7～0.9 mm",
                    score=0.87,
                    metadata={
                        "source": "维修手册_41页_分块整理.xlsx",
                        "page": 3,
                        "chapter": "一、火花塞",
                        "section": "1.2 检查火花塞",
                        "block_type": "检查标准",
                        "chunk_id": "manual:p3:r5",
                    },
                )
            ]
        )
    )

    evidence = await retriever.search("火花塞间隙是多少？", "CG125")

    assert len(evidence) == 1
    assert isinstance(evidence[0], EvidenceItem)
    assert evidence[0].source == "维修手册_41页_分块整理.xlsx"
    assert evidence[0].page == 3
    assert evidence[0].score == 0.87
    assert "0.7～0.9 mm" in evidence[0].snippet
    assert evidence[0].metadata == {
        "chapter": "一、火花塞",
        "section": "1.2 检查火花塞",
        "block_type": "检查标准",
        "chunk_id": "manual:p3:r5",
    }


@pytest.mark.anyio
async def test_retriever_returns_empty_when_index_load_fails(
    tmp_path: Path,
) -> None:
    retriever = Retriever(index_path=tmp_path / "missing_index")

    evidence = await retriever.search("火花塞间隙是多少？", "CG125")

    assert evidence == []


@pytest.mark.anyio
async def test_retriever_returns_empty_when_query_fails() -> None:
    retriever = Retriever(vector_retriever=_FailingVectorRetriever())

    evidence = await retriever.search("火花塞间隙是多少？", "CG125")

    assert evidence == []


@pytest.mark.anyio
async def test_retriever_returns_empty_when_query_has_no_results() -> None:
    retriever = Retriever(vector_retriever=_FakeVectorRetriever([]))

    evidence = await retriever.search("火花塞间隙是多少？", "CG125")

    assert evidence == []


@pytest.mark.anyio
async def test_retriever_can_read_explicit_chunks_keyword_fallback(
    tmp_path: Path,
) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    chunks_path.write_text(
        json.dumps(
            {
                "chunk_id": "manual:p3:r5",
                "source": "维修手册_41页_分块整理.xlsx",
                "page": 3,
                "chapter": "一、火花塞",
                "section": "1.2 检查火花塞",
                "text": "火花塞间隙标准值：0.7～0.9 mm",
                "block_type": "检查标准",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    retriever = Retriever(
        chunks_path=chunks_path,
        index_path=tmp_path / "missing_index",
    )

    evidence = await retriever.search("火花塞间隙是多少？", "CG125")

    assert len(evidence) == 1
    assert evidence[0].metadata["chunk_id"] == "manual:p3:r5"


class _FakeVectorRetriever:
    def __init__(self, nodes: list[_FakeNodeWithScore]) -> None:
        self.nodes = nodes

    def retrieve(self, question: str) -> list[_FakeNodeWithScore]:
        del question
        return self.nodes


class _FailingVectorRetriever:
    def retrieve(self, question: str) -> list[_FakeNodeWithScore]:
        del question
        raise RuntimeError("query failed")


class _FakeNodeWithScore:
    def __init__(self, text: str, score: float, metadata: dict[str, object]) -> None:
        self.node = _FakeNode(text=text, metadata=metadata)
        self.score = score


class _FakeNode:
    def __init__(self, text: str, metadata: dict[str, object]) -> None:
        self.text = text
        self.metadata = metadata

    def get_content(self, metadata_mode: str = "none") -> str:
        del metadata_mode
        return self.text
