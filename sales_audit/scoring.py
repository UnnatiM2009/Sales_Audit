"""Scoring primitives: one audit line, its achievement, score and RAG."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NOT_SCORED = "NS"


def pct(numerator: float, denominator: float, digits: int = 1) -> float:
    """Percentage, returning 0.0 rather than raising on a zero denominator."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, digits)


def achievement(actual: float | None, norm: float | None,
                higher_is_better: bool = True) -> float | None:
    """Achievement against a norm, capped at 100%.

    Returns None when either side is unavailable, which marks the line
    'not scored' rather than silently scoring it zero.
    """
    if actual is None or norm in (None, 0):
        return None
    if higher_is_better:
        ratio = actual / norm
    else:
        # For "lower is better" norms (e.g. max 20% aged enquiries), full marks
        # at or below the norm, degrading as the actual overshoots.
        ratio = norm / actual if actual > 0 else 1.0
    return round(min(ratio, 1.0) * 100, 1)


@dataclass
class Line:
    """One scored parameter of the audit."""

    code: str                       # e.g. "6.3"
    pillar: str                     # e.g. "F_profitability"
    name: str
    checkpoint: str                 # what the auditor verifies
    norm_text: str
    weight: float
    actual_text: str = "—"
    achievement: float | None = None
    source: str = "Not supplied"
    remark: str = ""
    unscored_reason: str = ""

    @property
    def scored(self) -> bool:
        return self.achievement is not None

    @property
    def score(self) -> float:
        if not self.scored:
            return 0.0
        return round(self.weight * self.achievement / 100, 2)

    def rag(self, cfg_rag: dict[str, int]) -> str:
        if not self.scored:
            return NOT_SCORED
        if self.achievement >= cfg_rag["green_min"]:
            return "Green"
        if self.achievement >= cfg_rag["amber_min"]:
            return "Amber"
        return "Red"

    def rag_remark(self, cfg_rag: dict[str, int]) -> str:
        if not self.scored:
            return f"Not scored — {self.unscored_reason or 'source data not supplied'}"
        return f"{self.rag(cfg_rag)} — {self.remark}" if self.remark else self.rag(cfg_rag)

    def display_achievement(self) -> str:
        return "—" if not self.scored else f"{self.achievement:.0f}%"

    def display_score(self) -> str:
        return NOT_SCORED if not self.scored else f"{self.score:.1f}"


@dataclass
class Pillar:
    key: str
    title: str
    weight: float
    lines: list[Line] = field(default_factory=list)

    @property
    def scorable_weight(self) -> float:
        return round(sum(l.weight for l in self.lines if l.scored), 2)

    @property
    def score(self) -> float:
        return round(sum(l.score for l in self.lines), 2)

    @property
    def achievement(self) -> float | None:
        sw = self.scorable_weight
        return round(self.score / sw * 100, 1) if sw else None

    def rag(self, cfg_rag: dict[str, int]) -> str:
        a = self.achievement
        if a is None:
            return NOT_SCORED
        if a >= cfg_rag["green_min"]:
            return "Green"
        if a >= cfg_rag["amber_min"]:
            return "Amber"
        return "Red"


@dataclass
class AuditResult:
    pillars: list[Pillar]
    context: dict[str, Any] = field(default_factory=dict)
    exceptions: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    actions: list[dict[str, str]] = field(default_factory=list)

    @property
    def all_lines(self) -> list[Line]:
        return [l for p in self.pillars for l in p.lines]

    @property
    def total_weight(self) -> float:
        return round(sum(p.weight for p in self.pillars), 2)

    @property
    def scorable_weight(self) -> float:
        return round(sum(p.scorable_weight for p in self.pillars), 2)

    @property
    def total_score(self) -> float:
        return round(sum(p.score for p in self.pillars), 2)

    @property
    def index(self) -> float | None:
        sw = self.scorable_weight
        return round(self.total_score / sw * 100, 1) if sw else None

    def band(self, grading: list[dict[str, Any]]) -> dict[str, Any]:
        idx = self.index
        if idx is None:
            return {"band": "Not scored", "response": "—", "action": "Supply source data and re-run"}
        for g in grading:
            if idx >= g["min"]:
                return g
        return grading[-1]
