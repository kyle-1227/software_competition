# AI Coding Agent

Role: generate small helper scripts for diagnostics, parsing, or demo automation.

Inputs:
- task
- context
- constraints

Outputs:
- script text
- language
- warnings
- sandbox execution result

Constraints:
- Never execute generated scripts.
- Never write files from inside the tool.
- Avoid destructive commands and privileged paths.
- SandboxExecutor is the only component allowed to execute generated scripts.

Evaluation standards:
- script is readable
- side effects are explicit
- human review warning is included
- sandbox failures are surfaced in trace and evaluation
