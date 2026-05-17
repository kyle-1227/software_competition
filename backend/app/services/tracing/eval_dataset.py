from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.tracing.eval_adapter import TraceEvalCase
from app.services.tracing.serializers import redact_trace_text, sanitize_trace_dict


def default_trace_eval_dataset_path() -> Path:
    return Path(__file__).resolve().parents[4] / "evals" / "datasets" / "trace_regression_cases.jsonl"


class TraceEvalDatasetWriter:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_trace_eval_dataset_path()

    def append_case(self, case: TraceEvalCase | dict[str, Any]) -> bool:
        payload = _sanitize_case_payload(_case_dict(case))
        case_id = str(payload.get("case_id") or "")
        if not case_id:
            raise ValueError("Trace eval case requires case_id")
        if case_id in self._existing_case_ids():
            return False

        line = json.dumps(payload, ensure_ascii=False, default=str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        return True

    def _existing_case_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("case_id"):
                        ids.add(str(payload["case_id"]))
        except OSError:
            return ids
        return ids


def _case_dict(case: TraceEvalCase | dict[str, Any]) -> dict[str, Any]:
    if isinstance(case, TraceEvalCase):
        return case.model_dump(mode="json")
    if isinstance(case, dict):
        return dict(case)
    raise TypeError("case must be TraceEvalCase or dict")


def _sanitize_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact_preview_fields(sanitize_trace_dict(payload))


def _redact_preview_fields(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {str(item_key): _redact_preview_fields(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_preview_fields(item) for item in value]
    normalized_key = str(key or "").lower()
    if isinstance(value, str) and (
        normalized_key.endswith("_preview") or "snippet" in normalized_key
    ):
        redacted = redact_trace_text(value)
        if len(redacted) <= 1:
            return redacted
        limit = min(120, len(redacted) - 1)
        return redacted[:limit].rstrip() + "..."
    return value
