from __future__ import annotations

from app.tools.manifest import ToolManifest


class ApprovalGate:
    def check(
        self,
        *,
        manifest: ToolManifest,
        risk_level: str,
        approved: bool = False,
    ) -> tuple[bool, str | None]:
        if manifest.requires_approval and risk_level == "high" and not approved:
            return False, f"{manifest.name} requires approval at high risk"
        return True, None
