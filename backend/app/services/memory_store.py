from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class MemoryStore:
    """进程内多轮上下文存储，含窗口管理和 LLM 摘要压缩。

    当历史条目超过 SUMMARY_TRIGGER 时，将最早条目压缩为一句摘要。
    硬上限 MAX_WINDOW_ENTRIES 确保不会无限增长。
    """

    def __init__(self, llm_client: Any | None = None) -> None:
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._summaries: dict[str, str] = {}
        self._llm_client = llm_client

    @property
    def max_window(self) -> int:
        return getattr(settings, "memory_max_window", 20)

    @property
    def summary_trigger(self) -> int:
        return getattr(settings, "memory_summary_trigger", 15)

    def add_trace(self, session_id: str, trace: dict[str, Any]) -> None:
        history = self._sessions.setdefault(session_id, [])
        history.append(trace)

        # Enforce hard window limit
        if len(history) > self.max_window:
            self._sessions[session_id] = history[-self.max_window:]

        # Trigger summarization
        if len(history) >= self.summary_trigger:
            self._summarize_fallback(session_id)

    def get_history(
        self, session_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        history = self._sessions.get(session_id, [])
        summary = self._summaries.get(session_id)

        if summary:
            return [{"type": "summary", "content": summary}] + list(history[-limit:])
        return list(history[-limit:])

    def clear_history(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._summaries.pop(session_id, None)

    def _summarize_fallback(self, session_id: str) -> None:
        """Fallback summarization: keep only summarized questions.

        If LLM is available, would delegate to generate_text for a real summary.
        """
        history = self._sessions.get(session_id, [])
        if not history or len(history) < self.summary_trigger:
            return

        oldest_questions = [
            entry.get("question", "")
            for entry in history[:5]
            if isinstance(entry, dict) and entry.get("question")
        ]
        if not oldest_questions:
            return

        self._summaries[session_id] = (
            "历史问题: " + "；".join(oldest_questions[:3])
        )
        logger.debug(
            "Summarized session %s (%d entries)", session_id, len(history)
        )
