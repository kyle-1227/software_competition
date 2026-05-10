# AI Coding Skill

PlanSteps:
- clarify script task
- generate minimal script
- include review warning
- return script as text

Tools:
- ai_coding

Failure handling:
- if the task requests dangerous operations, return a refusal with a safe alternative
- if required inputs are missing, return a scaffold with TODO markers

Evidence requirements:
- script generation must reference the task and not claim execution success
