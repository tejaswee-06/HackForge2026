from __future__ import annotations
from copy import deepcopy
from .evidence import Hypothesis, score_hypothesis


def run_falsification(hypothesis: Hypothesis, remove_ids: list[str] | None = None,
                      downgrade_ids: list[str] | None = None,
                      downgrade_factor: float = 0.5) -> dict:
    """Recompute the score after evidence is removed or downgraded."""
    if not 0 <= downgrade_factor <= 1:
        raise ValueError("downgrade_factor must be in [0, 1].")
    remove_ids = set(remove_ids or [])
    downgrade_ids = set(downgrade_ids or [])
    baseline = score_hypothesis(hypothesis)
    changed = deepcopy(hypothesis)
    kept = []
    for item in changed.evidence:
        if item.evidence_id in remove_ids:
            continue
        if item.evidence_id in downgrade_ids:
            item.reliability = round(item.reliability * downgrade_factor, 6)
        kept.append(item)
    changed.evidence = kept
    after = score_hypothesis(changed)
    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "baseline_score": baseline["heuristic_score"],
        "counterfactual_score": after["heuristic_score"],
        "score_delta": round(after["heuristic_score"] - baseline["heuristic_score"], 4),
        "removed_evidence_ids": sorted(remove_ids),
        "downgraded_evidence_ids": sorted(downgrade_ids),
        "downgrade_factor": downgrade_factor,
        "baseline": baseline,
        "counterfactual": after,
        "robustness_note": (
            "Small score change indicates limited sensitivity to the selected evidence under this heuristic; "
            "large change indicates dependence. This is not statistical certainty."
        ),
    }
