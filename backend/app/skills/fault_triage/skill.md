# Fault Triage Skill

PlanSteps:
- normalize device and symptom
- call manual_lookup
- compare symptom with evidence
- draft likely diagnosis
- pass answer through compliance_check

Tools:
- manual_lookup
- compliance_check

Failure handling:
- if no evidence is found, ask for device model, symptoms, or manual upload
- if safety risk is high, return a stop-and-escalate answer

Evidence requirements:
- answer must include the source and page when available
