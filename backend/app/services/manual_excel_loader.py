from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_PATH = (
    ROOT_DIR / "data" / "processed" / "manuals" / "维修手册_41页_分块整理.xlsx"
)
DEFAULT_OUTPUT_PATH = ROOT_DIR / "data" / "processed" / "manual_chunks.jsonl"
DEFAULT_SHEET_NAME = "分块索引"
DEFAULT_MANUAL_ID = "motorcycle_engine_manual"

REQUIRED_COLUMNS = ("页码", "章节", "小节", "分块内容", "关键词", "块类型")
REQUIRED_VALUE_COLUMNS = ("页码", "章节", "小节", "分块内容", "块类型")
KEYWORD_SPLIT_RE = re.compile(r"[、,，;；\n\r\t]+")

SPREADSHEET_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
WORKBOOK_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


@dataclass(frozen=True)
class RawExcelRow:
    row_number: int
    cells: list[str]


@dataclass(frozen=True)
class ManualExcelRow:
    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class WorksheetRef:
    name: str
    path: str


def convert_excel_to_jsonl(
    source_path: str | Path = DEFAULT_SOURCE_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    sheet_name: str = DEFAULT_SHEET_NAME,
    manual_id: str = DEFAULT_MANUAL_ID,
    allow_single_sheet_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Convert the manual chunk Excel workbook into newline-delimited JSON chunks."""

    source = Path(source_path)
    output = Path(output_path)
    rows = read_manual_rows(
        source,
        sheet_name=sheet_name,
        allow_single_sheet_fallback=allow_single_sheet_fallback,
    )
    chunks = rows_to_chunks(rows, manual_id=manual_id, source=source.name)
    write_jsonl(chunks, output)
    return chunks


def read_manual_rows(
    source_path: str | Path,
    *,
    sheet_name: str = DEFAULT_SHEET_NAME,
    allow_single_sheet_fallback: bool = True,
) -> list[ManualExcelRow]:
    raw_rows = read_xlsx_sheet_rows(
        Path(source_path),
        sheet_name=sheet_name,
        allow_single_sheet_fallback=allow_single_sheet_fallback,
    )
    if not raw_rows:
        raise ValueError("Manual Excel sheet is empty.")

    header_index = _first_non_empty_row_index(raw_rows)
    if header_index is None:
        raise ValueError("Manual Excel sheet does not contain a header row.")

    header_row = raw_rows[header_index]
    column_indexes = _validate_header(header_row.cells)
    rows: list[ManualExcelRow] = []
    for raw_row in raw_rows[header_index + 1 :]:
        if not any(cell.strip() for cell in raw_row.cells):
            continue
        rows.append(
            ManualExcelRow(
                row_number=raw_row.row_number,
                values={
                    column: _clean_cell(_cell_at(raw_row.cells, index))
                    for column, index in column_indexes.items()
                },
            )
        )
    return rows


def rows_to_chunks(
    rows: Sequence[ManualExcelRow],
    *,
    manual_id: str = DEFAULT_MANUAL_ID,
    source: str,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    errors: list[str] = []

    for row in rows:
        missing_values = [
            column for column in REQUIRED_VALUE_COLUMNS if not row.values.get(column)
        ]
        if missing_values:
            errors.append(
                f"row {row.row_number}: missing values for {', '.join(missing_values)}"
            )
            continue

        try:
            page = _parse_page(row.values["页码"], row.row_number)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        chapter = row.values["章节"]
        section = row.values["小节"]
        block_type = row.values["块类型"]
        content = row.values["分块内容"]
        keywords = _split_keywords(row.values.get("关键词", ""))
        text = _compose_text(section=section, content=content)

        chunks.append(
            {
                "chunk_id": f"manual:p{page}:r{row.row_number}",
                "manual_id": manual_id,
                "source": source,
                "page": page,
                "chapter": chapter,
                "section": section,
                "text": text,
                "keywords": keywords,
                "block_type": block_type,
                "metadata": {
                    "chapter": chapter,
                    "section": section,
                    "block_type": block_type,
                    "source_type": "excel_manual_chunks",
                },
            }
        )

    if errors:
        sample = "\n".join(errors[:10])
        suffix = f"\n... and {len(errors) - 10} more errors" if len(errors) > 10 else ""
        raise ValueError(f"Invalid manual Excel rows:\n{sample}{suffix}")

    return chunks


def write_jsonl(chunks: Sequence[dict[str, Any]], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as file:
        for chunk in chunks:
            file.write(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


def read_xlsx_sheet_rows(
    source_path: Path,
    *,
    sheet_name: str,
    allow_single_sheet_fallback: bool,
) -> list[RawExcelRow]:
    if not source_path.exists():
        raise FileNotFoundError(f"Manual Excel source not found: {source_path}")

    with ZipFile(source_path) as archive:
        worksheet = _resolve_worksheet(
            archive,
            sheet_name=sheet_name,
            allow_single_sheet_fallback=allow_single_sheet_fallback,
        )
        shared_strings = _read_shared_strings(archive)
        root = ET.fromstring(archive.read(worksheet.path))

    rows: list[RawExcelRow] = []
    for row in root.findall(".//x:sheetData/x:row", SPREADSHEET_NS):
        row_number = int(row.attrib.get("r", len(rows) + 1))
        cells: list[str] = []
        for cell in row.findall("x:c", SPREADSHEET_NS):
            column_index = _column_index(cell.attrib.get("r", "A"))
            while len(cells) < column_index:
                cells.append("")
            cells.append(_cell_value(cell, shared_strings))
        rows.append(RawExcelRow(row_number=row_number, cells=cells))
    return rows


def _resolve_worksheet(
    archive: ZipFile,
    *,
    sheet_name: str,
    allow_single_sheet_fallback: bool,
) -> WorksheetRef:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rel_root}

    worksheets: list[WorksheetRef] = []
    for sheet in workbook.findall("x:sheets/x:sheet", SPREADSHEET_NS):
        relation_id = sheet.attrib[f"{{{WORKBOOK_REL_NS}}}id"]
        target = rels[relation_id]
        worksheets.append(
            WorksheetRef(
                name=sheet.attrib["name"],
                path=_normalize_workbook_target(target),
            )
        )

    for worksheet in worksheets:
        if worksheet.name == sheet_name:
            return worksheet

    if allow_single_sheet_fallback and len(worksheets) == 1:
        return worksheets[0]

    available = ", ".join(worksheet.name for worksheet in worksheets) or "(none)"
    raise ValueError(f"Worksheet '{sheet_name}' not found. Available sheets: {available}")


def _read_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("x:si", SPREADSHEET_NS):
        values.append("".join(text.text or "" for text in item.findall(".//x:t", SPREADSHEET_NS)))
    return values


def _cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//x:t", SPREADSHEET_NS))

    value_node = cell.find("x:v", SPREADSHEET_NS)
    if value_node is None:
        return ""

    value = value_node.text or ""
    if cell.attrib.get("t") == "s" and value:
        return shared_strings[int(value)]
    return value


def _validate_header(headers: Sequence[str]) -> dict[str, int]:
    column_indexes: dict[str, int] = {}
    duplicates: set[str] = set()

    for index, value in enumerate(headers):
        header = _normalize_header(value)
        if not header:
            continue
        if header in column_indexes:
            duplicates.add(header)
            continue
        column_indexes[header] = index

    if duplicates:
        raise ValueError(f"Duplicate columns in manual Excel header: {', '.join(sorted(duplicates))}")

    missing = [column for column in REQUIRED_COLUMNS if column not in column_indexes]
    if missing:
        raise ValueError(f"Missing required columns in manual Excel: {', '.join(missing)}")

    return {column: column_indexes[column] for column in REQUIRED_COLUMNS}


def _first_non_empty_row_index(rows: Sequence[RawExcelRow]) -> int | None:
    for index, row in enumerate(rows):
        if any(cell.strip() for cell in row.cells):
            return index
    return None


def _normalize_workbook_target(target: str) -> str:
    cleaned = target.lstrip("/")
    if cleaned.startswith("xl/"):
        return cleaned
    return f"xl/{cleaned}"


def _column_index(reference: str) -> int:
    letters = "".join(char for char in reference if char.isalpha()) or "A"
    index = 0
    for char in letters:
        index = index * 26 + ord(char.upper()) - ord("A") + 1
    return index - 1


def _normalize_header(value: str) -> str:
    return value.replace("\ufeff", "").strip()


def _cell_at(cells: Sequence[str], index: int) -> str:
    return cells[index] if index < len(cells) else ""


def _clean_cell(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()


def _split_keywords(value: str) -> list[str]:
    return [keyword for keyword in (_clean_cell(item) for item in KEYWORD_SPLIT_RE.split(value)) if keyword]


def _compose_text(*, section: str, content: str) -> str:
    if not section:
        return content

    if content.startswith(section):
        remainder = content[len(section) :].lstrip()
        return f"{section}\n{remainder}" if remainder else section

    return f"{section}\n{content}" if content else section


def _parse_page(value: str, row_number: int) -> int:
    try:
        page_as_float = float(value)
    except ValueError as exc:
        raise ValueError(f"row {row_number}: page must be a number, got {value!r}") from exc

    page = int(page_as_float)
    if page_as_float != page or page <= 0:
        raise ValueError(f"row {row_number}: page must be a positive integer, got {value!r}")
    return page


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert manual chunk Excel sheet into manual_chunks.jsonl."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--sheet", default=DEFAULT_SHEET_NAME)
    parser.add_argument("--manual-id", default=DEFAULT_MANUAL_ID)
    parser.add_argument(
        "--strict-sheet",
        action="store_true",
        help="Require the requested sheet name even when the workbook has only one sheet.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    chunks = convert_excel_to_jsonl(
        source_path=args.source,
        output_path=args.output,
        sheet_name=args.sheet,
        manual_id=args.manual_id,
        allow_single_sheet_fallback=not args.strict_sheet,
    )
    print(f"wrote {len(chunks)} chunks to {args.output}")


if __name__ == "__main__":
    main()
