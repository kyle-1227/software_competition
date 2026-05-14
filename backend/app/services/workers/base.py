from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseWorker(ABC):
    """Worker 基类：每个 Worker 是一个独立的小流水线。

    Worker 是无状态的——只从 state 读取数据，返回 state update dict。
    """

    name: str
    description: str
    tools: list[str] = []

    @abstractmethod
    async def execute(
        self, state: dict[str, Any], services: Any
    ) -> dict[str, Any]:
        """执行 Worker 流水线，返回 state update dict。"""
        raise NotImplementedError
