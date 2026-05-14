from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.schemas.trace import Trace


@runtime_checkable
class LLMClient(Protocol):
    """LLM client capable of text and JSON generation."""

    async def generate_text(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> Any: ...

    async def generate_json(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> Any: ...


@runtime_checkable
class IToolRegistry(Protocol):
    """Registry that holds tools and executes them by name."""

    def register(self, tool: Any) -> None: ...

    def get(self, name: str) -> Any: ...

    async def execute(self, name: str, payload: dict[str, Any]) -> Any: ...


@runtime_checkable
class IMemoryStore(Protocol):
    """Session-scoped conversation memory."""

    def add_trace(self, session_id: str, trace: dict[str, Any]) -> None: ...

    def get_history(
        self, session_id: str, limit: int = 10
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class ITraceStore(Protocol):
    """Structured trace storage with nested spans."""

    def start_trace(self, session_id: str, question: str) -> Trace: ...

    def add_span(
        self, trace_id: str, span: Any, parent_span_id: str | None = None
    ) -> None: ...

    def get_trace(self, trace_id: str) -> Trace | None: ...

    def close_trace(self, trace_id: str) -> Trace | None: ...


@runtime_checkable
class ISandboxExecutor(Protocol):
    """Script execution sandbox."""

    def execute(self, script: str, language: str) -> Any: ...


@runtime_checkable
class IEvaluator(Protocol):
    """Answer evaluation for safety, compliance, and confidence."""

    def evaluate(self, answer: str, evidence: list[Any], tool_calls: list[Any]) -> Any: ...
