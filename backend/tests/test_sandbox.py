from app.services.sandbox import SandboxExecutor


def test_sandbox_executes_safe_python_script() -> None:
    result = SandboxExecutor().execute("print('ok')", "python")

    assert result.allowed is True
    assert result.return_code == 0
    assert "ok" in result.stdout


def test_sandbox_executes_safe_sql_script() -> None:
    result = SandboxExecutor().execute(
        "CREATE TABLE t (name TEXT); INSERT INTO t VALUES ('ok'); SELECT name FROM t;",
        "sql",
    )

    assert result.allowed is True
    assert result.return_code == 0
    assert "ok" in result.stdout


def test_sandbox_executes_safe_shell_script() -> None:
    result = SandboxExecutor().execute("Write-Output 'ok'", "shell")

    assert result.allowed is True
    assert result.return_code == 0
    assert "ok" in result.stdout


def test_sandbox_rejects_dangerous_script() -> None:
    result = SandboxExecutor().execute("Remove-Item -Recurse C:\\temp", "shell")

    assert result.allowed is False
    assert result.error is not None
