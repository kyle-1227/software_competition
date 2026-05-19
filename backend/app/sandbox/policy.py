from __future__ import annotations

import ast


class SandboxPolicy:
    dangerous_terms = (
        "rm ",
        "rm -",
        "del ",
        "erase ",
        "format",
        "shutdown",
        "reboot",
        "curl",
        "wget",
        "invoke-webrequest",
        "start-process",
        "remove-item",
        "sudo",
        "chmod",
        "chown",
    )
    blocked_names = {
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

    def validate_script(self, script: str, language: str) -> tuple[bool, str | None]:
        if len(script) > 8000:
            return False, "script is too long"
        lowered = script.lower()
        for term in self.dangerous_terms:
            if term in lowered:
                return False, f"script contains dangerous operation: {term.strip()}"
        if language == "python":
            return self._validate_python(script)
        if language == "sql":
            return self._validate_sql(script)
        if language == "shell":
            return False, "Shell execution is disabled by default."
        return False, f"unsupported script language: {language}"

    def _validate_python(self, script: str) -> tuple[bool, str | None]:
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            return False, f"Python syntax error: {exc}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return False, "Python import is not allowed."
            if isinstance(node, ast.Name) and (
                node.id.startswith("__") or node.id in self.blocked_names
            ):
                return False, f"Python identifier is blocked: {node.id}"
            if isinstance(node, ast.Attribute) and (
                node.attr.startswith("__") or node.attr in self.blocked_names
            ):
                return False, f"Python attribute is blocked: {node.attr}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in self.blocked_names:
                    return False, f"Python call is blocked: {node.func.id}"
        return True, None

    def _validate_sql(self, script: str) -> tuple[bool, str | None]:
        statements = [part.strip() for part in script.split(";") if part.strip()]
        for statement in statements:
            lowered = statement.lower()
            if not lowered.startswith("select"):
                return False, "SQL only allows SELECT statements."
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
                return False, "SQL only allows SELECT statements."
        return True, None
