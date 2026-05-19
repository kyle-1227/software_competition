from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolManifest(BaseModel):
    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_callers: list[str] = Field(default_factory=lambda: ["*"])
    max_risk_level: str = "medium"
    requires_approval: bool = False
    budget_units: int = 1


def build_default_tool_manifests(tool_registry: Any) -> dict[str, ToolManifest]:
    manifests: dict[str, ToolManifest] = {}
    tools = getattr(tool_registry, "_tools", {})
    for name, tool in tools.items():
        manifests[name] = ToolManifest(
            name=name,
            description=getattr(tool, "description", ""),
            parameters_schema=getattr(tool, "parameters_schema", {}) or {},
            allowed_callers=_default_callers(name),
            max_risk_level=_default_max_risk(name),
            requires_approval=name == "sandbox_execute",
            budget_units=2 if name in {"ai_coding", "sandbox_execute"} else 1,
        )
    if "manual_lookup" not in manifests:
        manifests["manual_lookup"] = ToolManifest(
            name="manual_lookup",
            description="Search maintenance manuals and return structured evidence.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "device_name": {"type": "string"},
                    "device_model": {"type": "string"},
                    "trace_id": {"type": "string"},
                },
                "required": ["question"],
            },
            allowed_callers=_default_callers("manual_lookup"),
            max_risk_level="high",
            budget_units=1,
        )
    if "ai_coding" not in manifests:
        manifests["ai_coding"] = ToolManifest(
            name="ai_coding",
            description="Generate reviewable diagnostic scripts.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "question": {"type": "string"},
                    "language": {"type": "string", "enum": ["python", "sql", "shell"]},
                },
                "required": ["task"],
            },
            allowed_callers=_default_callers("ai_coding"),
            max_risk_level="medium",
            budget_units=2,
        )
    if "compliance_check" not in manifests:
        manifests["compliance_check"] = ToolManifest(
            name="compliance_check",
            description="Check answer safety and compliance.",
            parameters_schema={"type": "object", "properties": {}},
            allowed_callers=_default_callers("compliance_check"),
            max_risk_level="high",
            budget_units=1,
        )
    if "sandbox_execute" not in manifests:
        manifests["sandbox_execute"] = ToolManifest(
            name="sandbox_execute",
            description="Execute generated code in the configured sandbox backend.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                    "language": {"type": "string", "enum": ["python", "sql", "shell"]},
                },
                "required": ["script", "language"],
            },
            allowed_callers=["ai_coding", "runtime"],
            max_risk_level="medium",
            requires_approval=True,
            budget_units=2,
        )
    return manifests


def _default_callers(tool_name: str) -> list[str]:
    if tool_name == "manual_lookup":
        return ["fault_triage", "sop_guidance", "runtime", "unknown", "general", "mixed"]
    if tool_name == "ai_coding":
        return ["ai_coding", "runtime", "unknown", "mixed"]
    if tool_name == "compliance_check":
        return ["fault_triage", "sop_guidance", "runtime", "unknown", "general", "mixed"]
    return ["*"]


def _default_max_risk(tool_name: str) -> str:
    if tool_name in {"ai_coding", "sandbox_execute"}:
        return "medium"
    return "high"
