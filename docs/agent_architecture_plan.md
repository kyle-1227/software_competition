# Agent Architecture Plan

This document mirrors the root-level architecture plan in a docs-friendly location.

The MVP Harness uses a deterministic Plan -> Tool -> Draft Answer -> Evaluator -> Trace flow. The production target is a skill-driven Agent Harness with manual lookup, multimodal retrieval, SOP guidance, compliance review, knowledge curation, and persistent trace storage.

Current implementation focus:

- `AgentHarness` orchestrates the workflow.
- `ToolRegistry` dispatches deterministic tools.
- `Retriever` exposes a LlamaIndex-compatible placeholder.
- `Evaluator` checks safety, compliance, and evidence confidence.
- `TraceStore` records plan, tools, evidence, answer, and evaluation.

Next steps:

- parse PDF manuals into chunks
- build a LlamaIndex vector store
- replace deterministic draft answers with an LLM chain
- persist traces and knowledge submissions
