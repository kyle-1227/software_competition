from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HarnessGradeResult:
    case_id: str
    grader: str
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def require(condition: bool, reason: str, reasons: list[str]) -> None:
    if not condition:
        reasons.append(reason)


def score_from_reasons(reasons: list[str]) -> float:
    return 1.0 if not reasons else 0.0
