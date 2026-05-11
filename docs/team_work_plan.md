# Team Work Plan

This docs copy summarizes the root-level team plan for delivery packaging.

Key milestones:

- 2026-05-17: MVP retrieval flow returns manual evidence.
- 2026-06-14: feature freeze.
- 2026-06-24: submission package freeze.

Responsibilities:

- Backend and Agent: API, Harness, tools, evaluator, tests.
- Data and Model: PDF parsing, chunking, indexing, retrieval, evaluation data.
- Frontend and Docs: UI integration, usage docs, product manual, test report, demo assets.

Immediate backend priority:

- keep `/api/query` stable
- add traceable Harness output
- preserve `/api/manuals/register`
- add `/api/manual/register` compatibility route
