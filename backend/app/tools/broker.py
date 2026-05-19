from __future__ import annotations

import time
from typing import Any

from app.policy.engine import PolicyEngine
from app.schemas.trace import SpanKind
from app.services.tool_registry import ToolResult
from app.services.tracing.context import trace_span
from app.tools.approval import ApprovalGate
from app.tools.budget import BudgetManager
from app.tools.manifest import ToolManifest
from app.tools.result import ResultVerifier


class ToolBroker:
    """Governed tool execution facade.

    Pipeline: schema validation -> policy -> approval -> budget -> execute ->
    result verification -> trace metadata.
    """

    def __init__(
        self,
        *,
        tool_registry: Any,
        manifests: dict[str, ToolManifest],
        policy_engine: PolicyEngine,
        approval_gate: ApprovalGate | None = None,
        budget_manager: BudgetManager | None = None,
        result_verifier: ResultVerifier | None = None,
        trace_store: Any = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.manifests = manifests
        self.policy_engine = policy_engine
        self.approval_gate = approval_gate or ApprovalGate()
        self.budget_manager = budget_manager or BudgetManager()
        self.result_verifier = result_verifier or ResultVerifier()
        self.trace_store = trace_store

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        caller: str = "unknown",
        risk_level: str = "unknown",
        trace_id: str | None = None,
        run_id: str | None = None,
        approved: bool = False,
    ) -> ToolResult:
        manifest = self.manifests.get(name)
        if manifest is None:
            return ToolResult(tool_name=name, success=False, error=f"Tool manifest not registered: {name}")

        async with trace_span(
            self.trace_store,
            trace_id,
            f"tool_broker.{name}",
            SpanKind.TOOL,
            inputs={"tool_name": name, "caller": caller, "risk_level": risk_level},
            metadata={"tool_name": name, "caller": caller, "risk_level": risk_level},
        ) as span:
            started = time.perf_counter()
            result = self._validate_schema(manifest, payload)
            if result is None:
                policy = self.policy_engine.evaluate(
                    manifest=manifest,
                    caller=caller,
                    risk_level=risk_level,
                )
                if not policy.allowed:
                    result = ToolResult(
                        tool_name=name,
                        success=False,
                        error=policy.reason,
                        metadata={"requires_approval": policy.requires_approval},
                    )
                else:
                    allowed, reason = self.approval_gate.check(
                        manifest=manifest,
                        risk_level=risk_level,
                        approved=approved,
                    )
                    if not allowed:
                        result = ToolResult(
                            tool_name=name,
                            success=False,
                            error=reason,
                            metadata={"requires_approval": True},
                        )
            if result is None:
                reserved, reason = self.budget_manager.reserve(
                    run_id=run_id or trace_id,
                    manifest=manifest,
                )
                if not reserved:
                    result = ToolResult(tool_name=name, success=False, error=reason)
            if result is None:
                result = await self.tool_registry.execute(name, payload)
                result = self.result_verifier.verify(result)

            duration_ms = int((time.perf_counter() - started) * 1000)
            result.metadata.setdefault("duration_ms", duration_ms)
            result.metadata.setdefault("caller", caller)
            result.metadata.setdefault("risk_level", risk_level)
            result.metadata.setdefault("brokered", True)
            span.set_metadata(
                {
                    "tool_name": name,
                    "caller": caller,
                    "risk_level": risk_level,
                    "success": result.success,
                    "duration_ms": result.metadata.get("duration_ms"),
                    "brokered": True,
                    "error": result.error,
                }
            )
            span.set_outputs(
                {
                    "tool_name": result.tool_name,
                    "success": result.success,
                    "error": result.error,
                }
            )
            return result

    def _validate_schema(
        self,
        manifest: ToolManifest,
        payload: dict[str, Any],
    ) -> ToolResult | None:
        schema = manifest.parameters_schema or {}
        required = schema.get("required")
        if isinstance(required, list):
            missing = [name for name in required if name not in payload]
            if missing:
                return ToolResult(
                    tool_name=manifest.name,
                    success=False,
                    error=f"missing required tool parameters: {', '.join(missing)}",
                )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, rules in properties.items():
                if key not in payload or not isinstance(rules, dict):
                    continue
                expected_type = rules.get("type")
                if expected_type and not self._matches_type(payload[key], expected_type):
                    return ToolResult(
                        tool_name=manifest.name,
                        success=False,
                        error=f"invalid type for tool parameter: {key}",
                    )
                enum = rules.get("enum")
                if isinstance(enum, list) and payload[key] not in enum:
                    return ToolResult(
                        tool_name=manifest.name,
                        success=False,
                        error=f"invalid value for tool parameter: {key}",
                    )
        return None

    def _matches_type(self, value: Any, expected_type: str) -> bool:
        if value is None:
            return True
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type in {"number", "integer"}:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return True
