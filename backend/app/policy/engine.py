from __future__ import annotations

from pydantic import BaseModel

from app.tools.manifest import ToolManifest

_RISK_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "blocked": 4}


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str = ""
    requires_approval: bool = False


class PolicyEngine:
    def evaluate(
        self,
        *,
        manifest: ToolManifest,
        caller: str,
        risk_level: str,
    ) -> PolicyDecision:
        if not self._caller_allowed(manifest, caller):
            return PolicyDecision(
                allowed=False,
                reason=f"caller {caller} is not allowed to execute {manifest.name}",
            )
        if _RISK_ORDER.get(risk_level, 0) > _RISK_ORDER.get(manifest.max_risk_level, 0):
            return PolicyDecision(
                allowed=False,
                reason=f"{manifest.name} is not allowed at risk level {risk_level}",
                requires_approval=True,
            )
        return PolicyDecision(
            allowed=True,
            requires_approval=manifest.requires_approval and risk_level == "high",
        )

    def _caller_allowed(self, manifest: ToolManifest, caller: str) -> bool:
        return "*" in manifest.allowed_callers or caller in manifest.allowed_callers
