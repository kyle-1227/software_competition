from __future__ import annotations

import json
import logging
from pathlib import Path

from app.schemas.trace import Trace, TraceSpan

logger = logging.getLogger(__name__)


class ConsoleExporter:
    """Prints a span tree to stdout on trace close."""

    def export(self, trace: Trace) -> None:
        lines = [f"Trace {trace.trace_id[:8]} - {trace.question[:60]}"]
        _render_span(trace.root_span, lines, indent=0)
        print("\n".join(lines))


class JsonFileExporter:
    """Appends trace JSON to a JSON Lines file."""

    def __init__(self, storage_dir: Path | str) -> None:
        self._dir = Path(storage_dir)

    def export(self, trace: Trace) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            filepath = self._dir / "traces.jsonl"
            with filepath.open("a", encoding="utf-8") as f:
                f.write(trace.model_dump_json() + "\n")
        except Exception as exc:
            logger.warning("Failed to export trace: %s", exc)


def _render_span(span: TraceSpan, lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    status_icon = {"ok": "+", "error": "!", "skipped": "-"}.get(
        span.status.value if hasattr(span.status, "value") else str(span.status), " "
    )
    duration = ""
    if span.end_time and span.start_time:
        ms = (span.end_time - span.start_time).total_seconds() * 1000
        duration = f" ({ms:.0f}ms)"

    lines.append(
        f"{prefix}{status_icon} {span.kind.value}:{span.name}{duration}"
    )
    if span.error:
        lines.append(f"{prefix}  ERR: {span.error}")

    for child in span.children:
        _render_span(child, lines, indent + 1)
