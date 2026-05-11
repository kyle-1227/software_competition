# AI Coding Prompt

Input fields:
- task: requested automation or script goal
- context: maintenance context and constraints
- language: python | sql | shell

Generate a small, reviewable script only.

Constraints:
- Do not execute commands.
- Do not write system directories.
- Do not include destructive operations.
- Include comments explaining required human review.

Output fields:
- language
- script
- execution_allowed
- warnings

SandboxExecutor is responsible for execution after safety checks.
