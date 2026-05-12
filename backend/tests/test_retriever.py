from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.query import EvidenceItem
from app.services.retriever import Retriever


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_generated_manual_jsonl_has_expected_shape() -> None:
    chunks_path = ROOT_DIR / "data" / "processed" / "manual_chunks.jsonl"
    chunks = [
        json.loads(line)
        for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    pages = [chunk["page"] for chunk in chunks if chunk["page"] is not None]
    assert len(chunks) == 118
    assert min(pages) == 1
    assert max(pages) == 41
    assert chunks[0]["chunk_id"].startswith("manual:p")
    assert chunks[0]["manual_id"] == "motorcycle_engine_manual"
    assert chunks[0]["metadata"]["source_type"] == "excel_manual_chunks"


@pytest.mark.anyio
async def test_retriever_returns_real_jsonl_evidence(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p3:r7",
                "manual_id": "motorcycle_engine_manual",
                "source": "维修手册_41页_分块整理.xlsx",
                "page": 3,
                "chapter": "一、火花塞",
                "section": "1.4 测量压缩压力",
                "text": "1.4 测量压缩压力\n1. 启动发动机。2. 安装压力表。3. 测量压缩压力。",
                "keywords": ["压缩压力", "压力表", "火花塞", "发动机"],
                "block_type": "测量步骤",
                "metadata": {
                    "chapter": "一、火花塞",
                    "section": "1.4 测量压缩压力",
                    "block_type": "测量步骤",
                    "source_type": "excel_manual_chunks",
                },
            },
            {
                "chunk_id": "manual:p12:r27",
                "manual_id": "motorcycle_engine_manual",
                "source": "维修手册_41页_分块整理.xlsx",
                "page": 12,
                "chapter": "四、气缸头与气门",
                "section": "4.3 凸轮轴 / 安装凸轮轴",
                "text": "安装凸轮轴时拉紧正时链条。",
                "keywords": ["凸轮轴", "正时链条"],
                "block_type": "安装步骤",
                "metadata": {
                    "chapter": "四、气缸头与气门",
                    "section": "4.3 凸轮轴 / 安装凸轮轴",
                    "block_type": "安装步骤",
                    "source_type": "excel_manual_chunks",
                },
            },
        ],
    )

    retriever = Retriever(chunks_path=chunks_path, top_k=1)
    evidence = await retriever.search("启动困难，怀疑压缩压力低怎么办", "CG125")

    assert len(evidence) == 1
    assert isinstance(evidence[0], EvidenceItem)
    assert evidence[0].source == "维修手册_41页_分块整理.xlsx"
    assert evidence[0].page == 3
    assert evidence[0].score is not None
    assert evidence[0].score > 0
    assert "压缩压力" in evidence[0].snippet
    assert evidence[0].metadata == {
        "chapter": "一、火花塞",
        "section": "1.4 测量压缩压力",
        "block_type": "测量步骤",
    }


@pytest.mark.anyio
async def test_retriever_uses_placeholder_when_jsonl_is_missing(tmp_path: Path) -> None:
    retriever = Retriever(chunks_path=tmp_path / "missing_manual_chunks.jsonl")

    evidence = await retriever.search("火花塞间隙应该怎么检查？", "CG125")

    assert len(evidence) == 1
    assert evidence[0].source == "manual::CG125"
    assert evidence[0].page is None
    assert evidence[0].score == 0.42
    assert evidence[0].metadata["retriever"] == "llama-index-placeholder"


@pytest.mark.anyio
async def test_retriever_returns_empty_list_for_no_jsonl_matches(tmp_path: Path) -> None:
    chunks_path = tmp_path / "manual_chunks.jsonl"
    _write_jsonl(
        chunks_path,
        [
            {
                "chunk_id": "manual:p12:r27",
                "manual_id": "motorcycle_engine_manual",
                "source": "维修手册_41页_分块整理.xlsx",
                "page": 12,
                "chapter": "四、气缸头与气门",
                "section": "4.3 凸轮轴 / 安装凸轮轴",
                "text": "安装凸轮轴时拉紧正时链条。",
                "keywords": ["凸轮轴", "正时链条"],
                "block_type": "安装步骤",
                "metadata": {
                    "chapter": "四、气缸头与气门",
                    "section": "4.3 凸轮轴 / 安装凸轮轴",
                    "block_type": "安装步骤",
                },
            }
        ],
    )

    retriever = Retriever(chunks_path=chunks_path)

    assert await retriever.search("轮胎气压是多少？") == []


@pytest.mark.anyio
async def test_default_retriever_recalls_compression_pressure_page() -> None:
    retriever = Retriever()

    evidence = await retriever.search("启动困难，怀疑压缩压力低怎么办", "摩托车发动机")

    assert evidence
    assert any(item.page == 3 for item in evidence)
    assert any("压缩压力" in item.snippet for item in evidence)


@pytest.mark.anyio
async def test_retriever_prioritizes_diagnostic_checks_over_parts_lists() -> None:
    retriever = Retriever(top_k=3)

    evidence = await retriever.search(
        "热车后怠速不稳，排气管偶尔回火，应该先检查哪里？",
        "示例型号",
    )

    assert evidence
    assert any("火花塞" in item.snippet for item in evidence)
    assert any("气门间隙" in item.snippet for item in evidence)
    assert all(item.metadata["block_type"] != "装配部件清单" for item in evidence[:3])


def _write_jsonl(path: Path, chunks: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
