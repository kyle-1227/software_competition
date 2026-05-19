from __future__ import annotations

from app.core.config import settings
from app.schemas.query import SandboxResult
from app.sandbox.backend import LocalDisabledSandboxBackend
from app.sandbox.docker_rootless import DockerRootlessSandboxBackend


class SandboxExecutor:
    """Compatibility facade over the configured sandbox backend."""

    def __init__(self, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = timeout_seconds or settings.sandbox_timeout_seconds
        if settings.sandbox_backend == "docker_rootless":
            self.backend = DockerRootlessSandboxBackend(
                timeout_seconds=self.timeout_seconds,
            )
        else:
            self.backend = LocalDisabledSandboxBackend(
                timeout_seconds=self.timeout_seconds,
            )

    def execute(self, script: str, language: str) -> SandboxResult:
        return self.backend.execute(script, language)
