from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.schemas.orchestrator import OrchestratorDecision
from app.schemas.trace import SpanKind
from app.services.tracing.context import trace_span
from app.services.tracing.helpers import span_count_items, summarize_worker_result
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
        selected_workers: list[tuple[str, BaseWorker]] = []

        for name in decision.workers:
            worker = self._workers.get(name)
            if worker is None:
                logger.warning("Worker not registered: %s", name)
                continue
            selected_workers.append((name, worker))

        if not selected_workers:
            return []

        total_workers = len(selected_workers)
        tasks = [
            self._execute_worker_with_span(
                worker,
                name,
                decision,
                state,
                services,
                worker_index=index,
                total_workers=total_workers,
            )
            for index, (name, worker) in enumerate(selected_workers, start=1)
        ]
        worker_names = [name for name, _ in selected_workers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        worker_results: list[dict[str, Any]] = []
        for name, result in zip(worker_names, results):
            if isinstance(result, Exception):
                logger.error("Worker %s failed: %s", name, result)
                worker_results.append(
                    {
                        "worker": name,
                        "error": str(result),
                        "worker_outputs": [{"worker": name, "error": str(result)}],
                    }
                )
            elif isinstance(result, dict):
                worker_results.append(result)
            else:
                worker_results.append(
                    {
                        "worker": name,
                        "result": result,
                        "worker_outputs": [{"worker": name, "result": result}],
                    }
                )

        return worker_results

    async def _execute_worker_with_span(
        self,
        worker: BaseWorker,
        name: str,
        decision: OrchestratorDecision,
        state: dict[str, Any],
        services: Any,
        *,
        worker_index: int,
        total_workers: int,
    ) -> Any:
        question = str(state.get("question") or "")
        async with trace_span(
            getattr(services, "trace_store", None),
            state.get("trace_id"),
            f"worker.{name}",
            SpanKind.NODE,
            inputs={
                "worker_name": name,
                "intent": getattr(decision, "intent", None),
                "question_preview": _preview(question),
                "evidence_count": span_count_items(state.get("evidence")),
                "tool_call_count": span_count_items(state.get("tool_calls")),
                "loop_decision_count": state.get("loop_decision_count", 0),
            },
            metadata={
                "worker_name": name,
                "intent": getattr(decision, "intent", None),
                "priority": getattr(decision, "priority", None),
                "selected_by_orchestrator": True,
                "worker_index": worker_index,
                "total_workers": total_workers,
            },
        ) as span:
            result = await worker.execute(state, services)
            if isinstance(result, dict):
                span.set_outputs(summarize_worker_result(result))
            else:
                span.set_outputs({"result_type": type(result).__name__})
            return result

    def register(self, worker: BaseWorker) -> None:
        self._workers[worker.name] = worker


def _preview(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else value[:limit].rstrip() + "..."
