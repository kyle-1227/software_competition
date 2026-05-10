import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.schemas.query import SandboxResult


class SandboxExecutor:
    """比赛演示级脚本执行器。

    该模块不是强隔离沙箱；它通过静态危险词检查、临时目录、超时和非交互
    执行降低风险。生产环境应替换为容器、微虚拟机或专用沙箱服务。
    """

    _dangerous_terms = (
        "rm ",
        "rm -",
        "del ",
        "erase ",
        "format",
        "shutdown",
        "reboot",
        "curl",
        "wget",
        "Invoke-WebRequest",
        "iwr ",
        "Start-Process",
        "Set-ExecutionPolicy",
        "reg ",
        "Remove-Item",
        "sudo",
        "chmod",
        "chown",
        ">/",
        "> c:\\",
        "> C:\\",
    )

    def __init__(self, timeout_seconds: int = 3) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(self, script: str, language: str) -> SandboxResult:
        normalized = language.lower().strip()
        started = time.perf_counter()
        allowed, reason = self._is_allowed(script)
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
        if normalized == "shell":
            return self._execute_shell(script, started)

        return SandboxResult(
            language=normalized,
            allowed=False,
            error=f"不支持的脚本类型：{language}",
            duration_ms=self._elapsed_ms(started),
        )

    def _execute_python(self, script: str, started: float) -> SandboxResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.py"
            script_path.write_text(script, encoding="utf-8")
            return self._run_process(
                [sys.executable, str(script_path)], "python", Path(tmpdir), started
            )

    def _execute_shell(self, script: str, started: float) -> SandboxResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.ps1"
            script_path.write_text(script, encoding="utf-8")
            return self._run_process(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                "shell",
                Path(tmpdir),
                started,
            )

    def _execute_sql(self, script: str, started: float) -> SandboxResult:
        try:
            connection = sqlite3.connect(":memory:")
            cursor = connection.cursor()
            statements = [part.strip() for part in script.split(";") if part.strip()]
            rows: list[tuple[object, ...]] = []
            for statement in statements:
                cursor.execute(statement)
                if statement.lower().startswith("select"):
                    rows.extend(cursor.fetchall())
            connection.commit()
            return SandboxResult(
                language="sql",
                allowed=True,
                return_code=0,
                stdout=str(rows) if rows else f"rows_affected={cursor.rowcount}",
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
            try:
                connection.close()
            except Exception:
                pass

    def _run_process(
        self,
        command: list[str],
        language: str,
        cwd: Path,
        started: float,
    ) -> SandboxResult:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            return SandboxResult(
                language=language,
                allowed=True,
                return_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                error=None if completed.returncode == 0 else completed.stderr,
                duration_ms=self._elapsed_ms(started),
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                language=language,
                allowed=True,
                return_code=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error="脚本执行超时。",
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

    def _is_allowed(self, script: str) -> tuple[bool, str | None]:
        lowered = script.lower()
        for term in self._dangerous_terms:
            if term.lower() in lowered:
                return False, f"脚本包含危险命令或高风险操作：{term.strip()}"
        return True, None

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
