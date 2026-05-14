from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.schemas.orchestrator import OrchestratorDecision
from app.services.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class WorkerDispatcher:
    """根据 OrchestratorDecision 并行 dispatch workers。

    独立的 workers 通过 asyncio.gather 并发执行，以减少总延迟。
    """

    def __init__(self, workers: dict[str, BaseWorker]) -> None:
        self._workers = workers

    async def dispatch(
        self,
        decision: OrchestratorDecision,
        state: dict[str, Any],
        services: Any,
    ) -> list[dict[str, Any]]:
        """并行执行指定的 workers，返回合并的 worker_outputs。"""
        tasks = []
        worker_names = []

        for name in decision.workers:
            worker = self._workers.get(name)
            if worker is None:
                logger.warning("Worker not registered: %s", name)
                continue
            tasks.append(worker.execute(state, services))
            worker_names.append(name)

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        worker_outputs: list[dict[str, Any]] = []
        for name, result in zip(worker_names, results):
            if isinstance(result, Exception):
                logger.error("Worker %s failed: %s", name, result)
                worker_outputs.append(
                    {"worker": name, "error": str(result)}
                )
            elif isinstance(result, dict):
                worker_outputs.extend(
                    result.get("worker_outputs", [result])
                )

        return worker_outputs

    def register(self, worker: BaseWorker) -> None:
        self._workers[worker.name] = worker
