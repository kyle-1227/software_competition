from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from app.services.manual_excel_loader import convert_excel_to_jsonl


def test_convert_excel_to_jsonl_writes_standard_chunks(tmp_path: Path) -> None:
    source = tmp_path / "manual.xlsx"
    output = tmp_path / "manual_chunks.jsonl"
    _write_xlsx(
        source,
        sheet_name="分块索引",
        rows=[
            ["页码", "章节", "小节", "分块内容", "关键词", "块类型"],
            [
                "3",
                "一、火花塞",
                "1.4 测量压缩压力",
                "1. 启动发动机，预热几分钟，然后熄火。",
                "压缩压力、压力表、火花塞、发动机",
                "测量步骤",
            ],
        ],
    )

    chunks = convert_excel_to_jsonl(source, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    chunk = json.loads(lines[0])
    assert chunks == [chunk]
    assert chunk == {
        "chunk_id": "manual:p3:r2",
        "manual_id": "motorcycle_engine_manual",
        "source": "manual.xlsx",
        "page": 3,
        "chapter": "一、火花塞",
        "section": "1.4 测量压缩压力",
        "text": "1.4 测量压缩压力\n1. 启动发动机，预热几分钟，然后熄火。",
        "keywords": ["压缩压力", "压力表", "火花塞", "发动机"],
        "block_type": "测量步骤",
        "metadata": {
            "chapter": "一、火花塞",
            "section": "1.4 测量压缩压力",
            "block_type": "测量步骤",
            "source_type": "excel_manual_chunks",
        },
    }
    assert "score" not in chunk
    assert "snippet" not in chunk


def test_convert_excel_to_jsonl_allows_single_sheet_fallback(tmp_path: Path) -> None:
    source = tmp_path / "manual.xlsx"
    output = tmp_path / "manual_chunks.jsonl"
    _write_xlsx(
        source,
        sheet_name="工作表1",
        rows=[
            ["页码", "章节", "小节", "分块内容", "关键词", "块类型"],
            ["2", "全书目录", "目录（第2页）", "目录内容", "", "目录"],
        ],
    )

    chunks = convert_excel_to_jsonl(source, output)

    assert chunks[0]["chunk_id"] == "manual:p2:r2"
    assert chunks[0]["keywords"] == []


def test_convert_excel_to_jsonl_validates_required_columns(tmp_path: Path) -> None:
    source = tmp_path / "manual.xlsx"
    output = tmp_path / "manual_chunks.jsonl"
    _write_xlsx(
        source,
        sheet_name="分块索引",
        rows=[
            ["页码", "章节", "小节", "分块内容", "关键词"],
            ["3", "一、火花塞", "1.4 测量压缩压力", "内容", "压缩压力"],
        ],
    )

    with pytest.raises(ValueError, match="Missing required columns.*块类型"):
        convert_excel_to_jsonl(source, output)


def _write_xlsx(path: Path, *, sheet_name: str, rows: list[list[str]]) -> None:
    shared_strings: list[str] = []
    shared_indexes: dict[str, int] = {}

    sheet_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_number, value in enumerate(row, start=1):
            text = str(value)
            if text not in shared_indexes:
                shared_indexes[text] = len(shared_strings)
                shared_strings.append(text)
            cell_ref = f"{_column_name(column_number)}{row_number}"
            cells.append(
                f'<c r="{cell_ref}" t="s"><v>{shared_indexes[text]}</v></c>'
            )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    with ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
                "</workbook>"
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                + "".join(f"<si><t>{escape(value)}</t></si>" for value in shared_strings)
                + "</sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(sheet_rows)}</sheetData>'
                "</worksheet>"
            ),
        )


def _column_name(column_number: int) -> str:
    name = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name
