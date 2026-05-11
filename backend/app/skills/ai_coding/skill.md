# AI Coding Skill

PlanSteps:
- clarify script task
- generate minimal script
- include review warning
- return script as text
- pass script to SandboxExecutor after safety checks
- record sandbox output in TraceStore

Tools:
- ai_coding
- SandboxExecutor
- TraceStore

Failure handling:
- if the task requests dangerous operations, return a refusal with a safe alternative
- if required inputs are missing, return a scaffold with TODO markers
- if sandbox rejects or fails, keep the script visible but mark execution as failed

Evidence requirements:
- script generation must reference the task and not claim execution success
