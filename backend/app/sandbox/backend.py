from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.schemas.query import SandboxResult
from app.sandbox.policy import SandboxPolicy


class LocalDisabledSandboxBackend:
    """Local restricted backend kept for tests and offline development."""

    def __init__(self, timeout_seconds: int = 5, policy: SandboxPolicy | None = None) -> None:
        self.timeout_seconds = timeout_seconds
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
        if normalized == "python":
            return self._execute_python(script, started)
        if normalized == "sql":
            return self._execute_sql(script, started)
        return SandboxResult(
            language=normalized,
            allowed=False,
            error=f"unsupported script language: {language}",
            duration_ms=self._elapsed_ms(started),
        )

    def _execute_python(self, script: str, started: float) -> SandboxResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.py"
            script_path.write_text(script, encoding="utf-8")
            env = {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            return self._run_process(
                [sys.executable, "-I", str(script_path)],
                "python",
                Path(tmpdir),
                started,
                env=env,
            )

    def _execute_sql(self, script: str, started: float) -> SandboxResult:
        connection = sqlite3.connect(":memory:")
        try:
            cursor = connection.cursor()
            rows: list[tuple[object, ...]] = []
            statements = [part.strip() for part in script.split(";") if part.strip()]
            for statement in statements:
                cursor.execute(statement)
                rows.extend(cursor.fetchall())
            connection.commit()
            return SandboxResult(
                language="sql",
                allowed=True,
                return_code=0,
                stdout=self._truncate(str(rows[:20]) if rows else f"rows_affected={cursor.rowcount}"),
                duration_ms=self._elapsed_ms(started),
            )
        except Exception as exc:
            return SandboxResult(
                language="sql",
                allowed=True,
                return_code=1,
                stderr=str(exc),
                error=str(exc),
                duration_ms=self._elapsed_ms(started),
            )
        finally:
            connection.close()

    def _run_process(
        self,
        command: list[str],
        language: str,
        cwd: Path,
        started: float,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
            return SandboxResult(
                language=language,
                allowed=True,
                return_code=completed.returncode,
                stdout=self._truncate(completed.stdout),
                stderr=self._truncate(completed.stderr),
                error=None if completed.returncode == 0 else self._truncate(completed.stderr),
                duration_ms=self._elapsed_ms(started),
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                language=language,
                allowed=True,
                return_code=124,
                stdout=self._truncate(exc.stdout or ""),
                stderr=self._truncate(exc.stderr or ""),
                error="script execution timed out",
                duration_ms=self._elapsed_ms(started),
            )
        except FileNotFoundError as exc:
            return SandboxResult(
                language=language,
                allowed=True,
                return_code=127,
                error=str(exc),
                duration_ms=self._elapsed_ms(started),
            )

    def _truncate(self, text: str, limit: int = 4000) -> str:
        return text[:limit]

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
