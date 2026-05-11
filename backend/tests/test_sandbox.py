from app.services.sandbox import SandboxExecutor


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
