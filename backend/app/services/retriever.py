from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.query import EvidenceItem


TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[#×./+\-～][A-Za-z0-9]+)*|[\u4e00-\u9fff]{2,}")
STOP_TOKENS = {
    "应该",
    "该先",
    "先检",
    "检查",
    "查哪",
    "哪里",
    "应该先",
    "该先检",
    "先检查",
    "检查哪",
    "查哪里",
    "怎么",
    "怎么办",
    "多少",
    "是否",
}
RELATED_TERMS = {
    "怠速": ["火花塞", "火花塞间隙", "压缩压力", "气门间隙", "发动机"],
    "不稳": ["火花塞", "火花塞间隙", "压缩压力", "气门间隙"],
    "回火": ["火花塞", "火花塞间隙", "压缩压力", "气门间隙", "进气门", "排气门"],
    "排气管": ["排气门", "气门间隙"],
    "热车": ["发动机", "压缩压力", "气门间隙"],
}
BLOCK_TYPE_MULTIPLIERS = {
    "目录": 0.2,
    "装配部件清单": 0.35,
    "检查标准": 1.6,
    "测量步骤": 1.25,
    "技术标准/参数": 1.25,
    "调整步骤": 1.1,
    "拆卸步骤": 0.75,
    "安装步骤": 0.8,
}


class Retriever:
    """Keyword retriever over manual chunks exported from Excel."""

    def __init__(
        self,
        chunks_path: Path | None = None,
        index_path: Path | None = None,
        top_k: int = 5,
    ) -> None:
        del index_path
        self.chunks_path = chunks_path or settings.data_path / "processed" / "manual_chunks.jsonl"
        self.top_k = top_k
        self._chunks: list[dict[str, Any]] | None = None

    async def search_evidence(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        if not self.chunks_path.exists():
            return [_placeholder_evidence(question=question, device_model=device_model)]

        query_tokens = expand_query_tokens(question, tokenize(question))
        if not query_tokens:
            return []

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for position, chunk in enumerate(self._load_chunks()):
            score = _score_chunk(question, query_tokens, chunk)
            if score > 0:
                scored.append((score, position, chunk))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            _chunk_to_evidence(chunk, score)
            for score, _, chunk in _select_ranked_chunks(scored, self.top_k)
        ]

    async def search(
        self, question: str, device_model: str | None = None
    ) -> list[EvidenceItem]:
        return await self.search_evidence(question, device_model)

    def _load_chunks(self) -> list[dict[str, Any]]:
        if self._chunks is not None:
            return self._chunks

        chunks: list[dict[str, Any]] = []
        with self.chunks_path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON in manual chunks file {self.chunks_path} "
                        f"at line {line_number}: {exc.msg}"
                    ) from exc

        self._chunks = chunks
        return chunks


def tokenize(text: str) -> list[str]:
    normalized = text.lower().replace("，", " ").replace("、", " ")
    tokens: list[str] = []
    for match in TOKEN_RE.findall(normalized):
        token = match.strip()
        if not token or token in STOP_TOKENS:
            continue
        tokens.append(token)
        if _is_cjk(token) and len(token) > 2:
            tokens.extend(
                item
                for item in (token[index : index + 2] for index in range(len(token) - 1))
                if item not in STOP_TOKENS
            )
            if len(token) > 3:
                tokens.extend(
                    item
                    for item in (token[index : index + 3] for index in range(len(token) - 2))
                    if item not in STOP_TOKENS
                )
    return _dedupe(tokens)


def expand_query_tokens(question: str, query_tokens: list[str]) -> list[str]:
    expanded = list(query_tokens)
    for trigger, related_terms in RELATED_TERMS.items():
        if trigger in question:
            expanded.extend(term.lower() for term in related_terms)
    return _dedupe(expanded)


def _score_chunk(
    question: str,
    query_tokens: list[str],
    chunk: dict[str, Any],
) -> float:
    question_lower = question.lower()
    keywords = [str(keyword).lower() for keyword in chunk.get("keywords", []) if keyword]
    chapter = str(chunk.get("chapter") or "").lower()
    section = str(chunk.get("section") or "").lower()
    text = str(chunk.get("text") or chunk.get("content") or "").lower()
    block_type = str(chunk.get("block_type") or "").lower()

    keyword_hits = {
        keyword
        for keyword in keywords
        if keyword in question_lower or any(token in keyword for token in query_tokens)
    }
    chapter_section_hits = _matched_terms(query_tokens, f"{chapter} {section}")
    text_hits = _matched_terms(query_tokens, text)
    multiplier = _block_type_multiplier(block_type)

    return float(
        (len(keyword_hits) * 2 + len(chapter_section_hits) + len(text_hits))
        * multiplier
    )


def _matched_terms(query_tokens: list[str], target: str) -> set[str]:
    if not target:
        return set()
    return {token for token in query_tokens if token in target}


def _chunk_to_evidence(chunk: dict[str, Any], score: float) -> EvidenceItem:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    chapter = chunk.get("chapter") or metadata.get("chapter")
    section = chunk.get("section") or metadata.get("section")
    block_type = chunk.get("block_type") or metadata.get("block_type")

    return EvidenceItem(
        source=str(chunk.get("source") or "维修手册_41页_分块整理.xlsx"),
        page=_optional_int(chunk.get("page")),
        snippet=_snippet(chunk),
        score=round(score, 4),
        metadata={
            "chapter": chapter,
            "section": section,
            "block_type": block_type,
        },
    )


def _snippet(chunk: dict[str, Any], limit: int = 220) -> str:
    text = str(chunk.get("text") or chunk.get("content") or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _select_ranked_chunks(
    scored: list[tuple[float, int, dict[str, Any]]],
    top_k: int,
) -> list[tuple[float, int, dict[str, Any]]]:
    selected: list[tuple[float, int, dict[str, Any]]] = []
    seen_sections: set[str] = set()

    for item in scored:
        section_key = _section_key(item[2])
        if section_key in seen_sections:
            continue
        selected.append(item)
        seen_sections.add(section_key)
        if len(selected) >= top_k:
            return selected

    selected_ids = {id(item[2]) for item in selected}
    for item in scored:
        if id(item[2]) in selected_ids:
            continue
        selected.append(item)
        if len(selected) >= top_k:
            return selected

    return selected


def _section_key(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    section = str(chunk.get("section") or metadata.get("section") or "")
    return section.split("/", 1)[0].strip().rstrip("：:")


def _block_type_multiplier(block_type: str) -> float:
    for key, multiplier in BLOCK_TYPE_MULTIPLIERS.items():
        if key.lower() in block_type:
            return multiplier
    return 1.0


def _placeholder_evidence(question: str, device_model: str | None) -> EvidenceItem:
    device_hint = device_model or "未知型号"
    return EvidenceItem(
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


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)
