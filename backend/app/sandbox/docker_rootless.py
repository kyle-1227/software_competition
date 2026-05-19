from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from app.core.config import settings
from app.schemas.query import SandboxResult
from app.sandbox.policy import SandboxPolicy


class DockerRootlessSandboxBackend:
    """Docker rootless-oriented backend for production deployment."""

    def __init__(
        self,
        image: str | None = None,
        timeout_seconds: int | None = None,
        policy: SandboxPolicy | None = None,
    ) -> None:
        self.image = image or settings.sandbox_docker_image
        self.timeout_seconds = timeout_seconds or settings.sandbox_timeout_seconds
        self.policy = policy or SandboxPolicy()

    def execute(self, script: str, language: str) -> SandboxResult:
        normalized = language.lower().strip()
        started = time.perf_counter()
        allowed, reason = self.policy.validate_script(script, normalized)
        if not allowed:
            return SandboxResult(
                language=normalized,
                allowed=False,
                error=reason,
                duration_ms=self._elapsed_ms(started),
            )
        if normalized != "python":
            return SandboxResult(
                language=normalized,
                allowed=False,
                error="docker_rootless backend currently supports python only",
                duration_ms=self._elapsed_ms(started),
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.py"
            script_path.write_text(script, encoding="utf-8")
            command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "65532:65532",
                "--cpus",
                "0.5",
                "--memory",
                "128m",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "-v",
                f"{script_path.as_posix()}:/work/script.py:ro",
                "-w",
                "/work",
                self.image,
                "python",
                "-I",
                "/work/script.py",
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                return SandboxResult(
                    language=normalized,
                    allowed=True,
                    return_code=completed.returncode,
                    stdout=self._truncate(completed.stdout),
                    stderr=self._truncate(completed.stderr),
                    error=None if completed.returncode == 0 else self._truncate(completed.stderr),
                    duration_ms=self._elapsed_ms(started),
                )
            except subprocess.TimeoutExpired as exc:
                return SandboxResult(
                    language=normalized,
                    allowed=True,
                    return_code=124,
                    stdout=self._truncate(exc.stdout or ""),
                    stderr=self._truncate(exc.stderr or ""),
                    error="docker sandbox execution timed out",
                    duration_ms=self._elapsed_ms(started),
                )
            except FileNotFoundError as exc:
                return SandboxResult(
                    language=normalized,
                    allowed=False,
                    return_code=127,
                    error=f"docker is unavailable: {exc}",
                    duration_ms=self._elapsed_ms(started),
                )

    def _truncate(self, text: str, limit: int = 4000) -> str:
        return text[:limit]

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
