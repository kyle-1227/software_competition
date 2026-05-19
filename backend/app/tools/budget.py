from __future__ import annotations

from collections import defaultdict

from app.tools.manifest import ToolManifest


class BudgetManager:
    def __init__(self, default_budget_units: int = 64) -> None:
        self.default_budget_units = default_budget_units
        self._usage: dict[str, int] = defaultdict(int)

    def reserve(self, *, run_id: str | None, manifest: ToolManifest) -> tuple[bool, str | None]:
        key = run_id or "default"
        next_usage = self._usage[key] + manifest.budget_units
        if next_usage > self.default_budget_units:
            return False, "tool budget exceeded"
        self._usage[key] = next_usage
        return True, None
