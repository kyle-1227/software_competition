from typing import Any


class MemoryStore:
    """进程内多轮上下文存储。

    当前用于比赛演示和测试；后续可以替换为 Redis、SQLite 或业务数据库，
    以支持跨进程和服务重启后的历史追踪。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    def add_trace(self, session_id: str, trace: dict[str, Any]) -> None:
        self._sessions.setdefault(session_id, []).append(trace)

    def get_history(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        history = self._sessions.get(session_id, [])
        if limit <= 0:
            return []
        return list(history[-limit:])

    def clear_history(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
