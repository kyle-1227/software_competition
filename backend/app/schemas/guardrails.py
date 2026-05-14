from pydantic import BaseModel, Field


class GuardrailResult(BaseModel):
    passed: bool
    reason: str | None = None
    risk_level: str = "low"  # low | medium | high | blocked
    blocked: bool = False
