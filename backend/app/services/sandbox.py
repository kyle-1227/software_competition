import ast
import hashlib
import io
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
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
    _blocked_names = {
        "__import__",
        "open",
        "exec",
        "eval",
        "compile",
        "input",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        "requests",
        "urllib",
        "multiprocessing",
        "threading",
        "ctypes",
        "pickle",
        "system",
        "popen",
        "remove",
        "unlink",
    }
    _allowed_builtins = {
        "print": print,
        "len": len,
        "range": range,
        "sum": sum,
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "sorted": sorted,
        "enumerate": enumerate,
        "zip": zip,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
    }

    def __init__(self, timeout_seconds: int = 3) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(self, script: str, language: str) -> SandboxResult:
        normalized = language.lower().strip()
        started = time.perf_counter()
        if len(script) > 8000:
            return SandboxResult(
                language=normalized,
                allowed=False,
                error="脚本过长，已拒绝执行。",
                duration_ms=self._elapsed_ms(started),
            )
        if normalized == "shell":
            return SandboxResult(
                language=normalized,
                allowed=False,
                error="Shell 默认拒绝执行。",
                duration_ms=self._elapsed_ms(started),
            )
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

        return SandboxResult(
            language=normalized,
            allowed=False,
            error=f"不支持的脚本类型：{language}",
            duration_ms=self._elapsed_ms(started),
        )

    def _execute_python(self, script: str, started: float) -> SandboxResult:
        ast_result = self._validate_python_ast(script)
        if ast_result is not None:
            return SandboxResult(
                language="python",
                allowed=False,
                error=ast_result,
                duration_ms=self._elapsed_ms(started),
            )
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
        try:
            connection = sqlite3.connect(":memory:")
            cursor = connection.cursor()
            statements = [part.strip() for part in script.split(";") if part.strip()]
            rows: list[tuple[object, ...]] = []
            for statement in statements:
                lowered = statement.lower()
                if any(
                    keyword in lowered
                    for keyword in (
                        "insert",
                        "update",
                        "delete",
                        "drop",
                        "alter",
                        "create",
                        "attach",
                        "detach",
                        "pragma",
                        "vacuum",
                        "replace",
                        "truncate",
                    )
                ):
                    return SandboxResult(
                        language="sql",
                        allowed=False,
                        error="SQL 仅允许 SELECT。",
                        duration_ms=self._elapsed_ms(started),
                    )
                if not statement.lower().startswith("select"):
                    return SandboxResult(
                        language="sql",
                        allowed=False,
                        error="SQL 仅允许 SELECT。",
                        duration_ms=self._elapsed_ms(started),
                    )
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

    def _validate_python_ast(self, script: str) -> str | None:
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            return f"Python 语法错误：{exc}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return "Python 禁止 import / from import。"
            if isinstance(node, ast.Name):
                if node.id.startswith("__") or node.id in self._blocked_names:
                    return f"Python 禁止使用标识符：{node.id}"
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__") or node.attr in self._blocked_names:
                    return f"Python 禁止访问属性：{node.attr}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in self._blocked_names:
                    return f"Python 禁止调用：{node.func.id}"
        return None

    def _truncate(self, text: str, limit: int = 4000) -> str:
        return text[:limit]

    def _elapsed_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
