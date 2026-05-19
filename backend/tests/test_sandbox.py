from app.services.sandbox import SandboxExecutor
from app.sandbox.docker_rootless import DockerRootlessSandboxBackend
from app.core.config import settings


def test_sandbox_executes_safe_python_script() -> None:
    result = SandboxExecutor().execute("print('ok')", "python")

    assert result.allowed is True
    assert result.return_code == 0
    assert "ok" in result.stdout


def test_sandbox_executes_safe_sql_script() -> None:
    result = SandboxExecutor().execute(
        "SELECT 'ok' AS name;",
        "sql",
    )

    assert result.allowed is True
    assert result.return_code == 0
    assert "ok" in result.stdout


def test_sandbox_executes_safe_shell_script() -> None:
    result = SandboxExecutor().execute("Write-Output 'ok'", "shell")

    assert result.allowed is False
    assert "Shell" in result.error or "拒绝" in result.error


def test_sandbox_rejects_dangerous_script() -> None:
    result = SandboxExecutor().execute("import os\nos.system('dir')", "python")

    assert result.allowed is False
    assert result.error is not None


def test_docker_rootless_backend_executes_simple_print(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, "kwargs": kwargs})
        return _Completed(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("app.sandbox.docker_rootless.subprocess.run", fake_run)

    result = DockerRootlessSandboxBackend(image="python:test").execute("print('ok')", "python")

    assert result.allowed is True
    assert result.return_code == 0
    assert result.stdout == "ok\n"
    assert calls[0]["command"][:2] == ["docker", "run"]
    assert "--network" in calls[0]["command"]
    assert "none" in calls[0]["command"]


def test_production_docker_backend_does_not_use_local_subprocess(monkeypatch) -> None:
    def local_run(*args, **kwargs):
        raise AssertionError("local subprocess must not run in docker_rootless mode")

    def docker_run(command, **kwargs):
        assert command[:2] == ["docker", "run"]
        return _Completed(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(settings, "sandbox_backend", "docker_rootless")
    monkeypatch.setattr("app.sandbox.backend.subprocess.run", local_run)
    monkeypatch.setattr("app.sandbox.docker_rootless.subprocess.run", docker_run)

    result = SandboxExecutor().execute("print('ok')", "python")

    assert result.allowed is True
    assert result.return_code == 0
    assert result.stdout == "ok\n"


class _Completed:
    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
