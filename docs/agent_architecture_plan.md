# Agent Architecture Plan

This document is the docs-friendly architecture entrypoint. The full root-level design remains in `../agent_architecture_plan.md`; the production evolution roadmap is tracked in [`production_agent_evolution_plan.md`](production_agent_evolution_plan.md).

## Current Architecture Snapshot

The current backend uses a LangGraph-backed Agent Harness:

- `AgentHarness` is the API-facing facade.
- `StateGraph(HarnessState)` runs a fixed workflow with local adaptive decisions.
- Guardrail, Memory, Orchestrator, Worker, Loop, Eval, Trace, Memory Save, and Finalize are separate workflow nodes.
- `ToolRegistry` dispatches `manual_lookup`, `ai_coding`, and `compliance_check`.
- `MemoryStore` provides session-window memory and fallback summarization.
- `SandboxExecutor` provides demonstration-level restricted Python / read-only SQL execution.
- Trace / Eval / Metrics / Retention / Cleanup already have a production-ready foundation.

## Production Evolution Focus

The next architecture milestone is not to make the graph more dynamic. The target is:

- fixed workflow topology
- adaptive behavior inside nodes
- unified Runtime contract
- reusable Tool / Memory / Sandbox layers
- traceable RuntimeEvent / RuntimeResult output

Priority order:

1. Runtime Contract / State Machine.
2. Workflow `RuntimeResult` output.
3. Tool / Worker / Policy.
4. Memory persistence and retrieval.
5. Sandbox backend isolation.
6. E2E tests, operational metrics, and eval feedback loop.

## Related Documents

- Production roadmap: [`production_agent_evolution_plan.md`](production_agent_evolution_plan.md)
- Production gaps: [`production_readiness_gaps.md`](production_readiness_gaps.md)
- Team split: [`team_work_plan.md`](team_work_plan.md)
