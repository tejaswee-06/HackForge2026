from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: str
    direction: Literal["supports", "contradicts", "context"]
    reliability: float = Field(ge=0.0, le=1.0)
    strength: float = Field(ge=0.0, le=1.0)
    source: str
    description: str
    collected_at: str | None = None
    source_timestamp: str | None = None
    verified: bool = False


class Hypothesis(BaseModel):
    hypothesis_id: str
    title: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


def score_hypothesis(hypothesis: Hypothesis) -> dict:
    """Transparent heuristic score; not a calibrated probability of guilt."""
    support = sum(e.reliability * e.strength for e in hypothesis.evidence if e.direction == "supports")
    contradiction = sum(e.reliability * e.strength for e in hypothesis.evidence if e.direction == "contradicts")
    context = sum(e.reliability * e.strength for e in hypothesis.evidence if e.direction == "context")
    denom = support + contradiction
    balance = 0.5 if denom == 0 else support / denom
    # Saturating evidence quantity term avoids unlimited score growth from duplicates.
    evidence_mass = 1.0 - pow(2.718281828, -0.35 * denom)
    score = max(0.0, min(1.0, 0.15 + 0.70 * balance * evidence_mass + 0.10 * min(context, 1.0)))
    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "title": hypothesis.title,
        "heuristic_score": round(score, 4),
        "support_weight": round(support, 4),
        "contradiction_weight": round(contradiction, 4),
        "context_weight": round(context, 4),
        "evidence_count": len(hypothesis.evidence),
        "verified_evidence_count": sum(1 for e in hypothesis.evidence if e.verified),
        "interpretation": "Heuristic investigative prioritisation score, not a probability of identity, guilt, or legal proof.",
        "evidence": [e.model_dump() for e in hypothesis.evidence],
    }
