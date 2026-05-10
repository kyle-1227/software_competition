# Draft Answer Prompt

Input fields:
- question: user maintenance question
- device: device name or model
- history: recent MemoryStore traces for the same device/session
- evidence: structured manual evidence
- tool_results: tool outputs from the Harness
- sandbox_result: AI Coding execution result when present

Write a concise maintenance answer with:
- safety prerequisite
- likely diagnosis path
- evidence references
- next action

Do not invent manual content. State uncertainty when evidence is weak.
Use history only as context; current evidence has higher priority than older traces.
