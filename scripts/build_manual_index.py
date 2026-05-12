from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(r"D:\DownLoad\页码_章节_小节_分块内容_关键词_块类型.xlsx")
PROCESSED_PATH = ROOT_DIR / "data" / "processed" / "manual_chunks.json"
INDEX_PATH = ROOT_DIR / "data" / "indexes" / "manual_keyword_index.json"

SPREADSHEET_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[#×./+\-～][A-Za-z0-9]+)*|[\u4e00-\u9fff]{2,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local manual search index from xlsx.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--chunks-out", type=Path, default=PROCESSED_PATH)
    parser.add_argument("--index-out", type=Path, default=INDEX_PATH)
    args = parser.parse_args()

    rows = read_xlsx_rows(args.source)
    chunks = build_chunks(rows)
    index = build_index(chunks)

    args.chunks_out.parent.mkdir(parents=True, exist_ok=True)
    args.index_out.parent.mkdir(parents=True, exist_ok=True)
    args.chunks_out.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.index_out.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"built {len(chunks)} chunks, pages {index['stats']['min_page']}-"
        f"{index['stats']['max_page']}"
    )
    print(f"chunks: {args.chunks_out}")
    print(f"index:  {args.index_out}")


def read_xlsx_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Excel source not found: {path}")

    with ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_name = _first_sheet_path(archive)
        root = ET.fromstring(archive.read(sheet_name))

    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", SPREADSHEET_NS):
        values: list[str] = []
        for cell in row.findall("x:c", SPREADSHEET_NS):
            index = _column_index(cell.attrib.get("r", "A"))
            while len(values) < index:
                values.append("")
            values.append(_cell_value(cell, shared_strings))
        rows.append(values)

    if not rows:
        return []

    headers = [_normalize_header(value) for value in rows[0]]
    data: list[dict[str, str]] = []
    for row in rows[1:]:
        item = {
            headers[index]: value.strip()
            for index, value in enumerate(row)
            if index < len(headers) and headers[index]
        }
        if item.get("分块内容"):
            data.append(item)
    return data


def build_chunks(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        keywords = _split_keywords(row.get("关键词", ""))
        content = _clean_space(row.get("分块内容", ""))
        chapter = _clean_space(row.get("章节", ""))
        section = _clean_space(row.get("小节", ""))
        block_type = _clean_space(row.get("块类型", ""))
        page = _parse_int(row.get("页码", ""))
        text_for_search = " ".join([chapter, section, block_type, content, " ".join(keywords)])
        chunks.append(
            {
                "id": f"manual-{index:04d}",
                "source": "摩托车发动机维修手册",
                "page": page,
                "chapter": chapter,
                "section": section,
                "block_type": block_type,
                "content": content,
                "keywords": keywords,
                "tokens": tokenize(text_for_search),
            }
        )
    return chunks


def build_index(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    postings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    doc_lengths: dict[str, int] = {}
    pages = [chunk["page"] for chunk in chunks if isinstance(chunk.get("page"), int)]

    for chunk in chunks:
        counts = Counter(chunk["tokens"])
        doc_lengths[chunk["id"]] = sum(counts.values())
        for token, count in sorted(counts.items()):
            postings[token].append({"id": chunk["id"], "tf": count})

    doc_count = len(chunks)
    idf = {
        token: round(math.log(1 + (doc_count - len(entries) + 0.5) / (len(entries) + 0.5)), 6)
        for token, entries in postings.items()
    }
    avg_doc_length = (
        sum(doc_lengths.values()) / len(doc_lengths) if doc_lengths else 0.0
    )

    return {
        "version": 1,
        "source": "摩托车发动机维修手册",
        "stats": {
            "chunk_count": doc_count,
            "min_page": min(pages) if pages else None,
            "max_page": max(pages) if pages else None,
            "avg_doc_length": round(avg_doc_length, 3),
        },
        "doc_lengths": doc_lengths,
        "idf": idf,
        "postings": postings,
    }


def tokenize(text: str) -> list[str]:
    normalized = text.lower().replace("，", " ").replace("、", " ")
    tokens: list[str] = []
    for match in TOKEN_RE.findall(normalized):
        token = match.strip()
        if not token:
            continue
        tokens.append(token)
        if _is_cjk(token) and len(token) > 2:
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))
            if len(token) > 3:
                tokens.extend(token[i : i + 3] for i in range(len(token) - 2))
    return tokens


def _read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("x:si", SPREADSHEET_NS):
        values.append("".join(text.text or "" for text in item.findall(".//x:t", SPREADSHEET_NS)))
    return values


def _first_sheet_path(archive: ZipFile) -> str:
    sheets = sorted(
        name
        for name in archive.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    if not sheets:
        raise ValueError("No worksheet found in xlsx file.")
    return sheets[0]


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    value_node = cell.find("x:v", SPREADSHEET_NS)
    if value_node is None:
        return ""
    value = value_node.text or ""
    if cell.attrib.get("t") == "s" and value:
        return shared_strings[int(value)]
    return value


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()) or "A"
    index = 0
    for char in letters:
        index = index * 26 + ord(char.upper()) - ord("A") + 1
    return index - 1


def _normalize_header(value: str) -> str:
    return value.strip()


def _split_keywords(value: str) -> list[str]:
    return [_clean_space(item) for item in re.split(r"[、,，;；]\s*", value) if item.strip()]


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_int(value: str) -> int | None:
    try:
        return int(float(value))
    except ValueError:
        return None


def _is_cjk(value: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in value)


if __name__ == "__main__":
    main()
