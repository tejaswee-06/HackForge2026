"""
ASTRA Person 2 — Explainable wallet/lead priority scoring.

This is a transparent PRIORITY SCORE (0–100), not a calibrated probability and
not proof of wallet ownership or criminality. It is designed to consume normalized
transaction/graph/OSINT signals from Person 1's backend.

Expected `signals` input:
{
  "model_illicit_score": 0.0..1.0,  # optional model output; score only, not proof
  "temporal": {
    "rapid_forwarding": 0..1,
    "fragmentation": 0..1,
    "consolidation": 0..1
  },
  "graph": {
    "incident_path_proximity": 0..1, # 1 means directly connected in observed graph
    "unusual_degree": 0..1,
    "flow_continuity": 0..1
  },
  "osint": {
    "source_quality": 0..1,
    "independent_corrob": 0..1,
    "indicator_match": 0..1,
    "source_freshness": 0..1
  },
  "evidence": {
    "contradiction_strength": 0..1,
    "missing_link_strength": 0..1
  }
}

All signal values must be evidence-derived and normalized by the caller. Missing
signals contribute zero and are explicitly listed in the result.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping
import math

WEIGHTS = {
    "model_illicit_score": 20.0,
    "temporal.rapid_forwarding": 8.0,
    "temporal.fragmentation": 7.0,
    "temporal.consolidation": 5.0,
    "graph.incident_path_proximity": 15.0,
    "graph.unusual_degree": 5.0,
    "graph.flow_continuity": 8.0,
    "osint.source_quality": 8.0,
    "osint.independent_corrob": 7.0,
    "osint.indicator_match": 7.0,
    "osint.source_freshness": 3.0,
}
PENALTIES = {
    "evidence.contradiction_strength": 12.0,
    "evidence.missing_link_strength": 7.0,
}

def _get_path(obj: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = obj
    for key in dotted_path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value

def _validate_signal(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a numeric value from 0 to 1")
    value = float(value)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be finite and within [0, 1]")
    return value

def score_lead(entity_id: str, signals: Mapping[str, Any], *, weights: Mapping[str, float] | None = None,
               penalties: Mapping[str, float] | None = None,
               not_applicable: Iterable[str] = ()) -> dict[str, Any]:
    """
    Return a deterministic, explainable priority score for a wallet/lead.

    `entity_id` is an address/identifier supplied by the caller. This function
    does not infer identity, fetch OSINT, or establish criminality.

    Integration extensions (backwards compatible; defaults reproduce astra-priority-v1 exactly):
    * ``weights`` / ``penalties``: override individual weights (sensitivity analysis). Weights must
      be finite and >= 0; unknown signal names are rejected.
    * ``not_applicable``: signals that cannot exist in this deployment (e.g. ``model_illicit_score``
      when no trained model is loaded). They are excluded from the normalising denominator and
      listed separately - this is different from a *missing* signal, which still counts in the
      denominator because the evidence could exist but was not found.
    """
    W = dict(WEIGHTS)
    P = dict(PENALTIES)
    for src, dst in ((weights, W), (penalties, P)):
        for k, v in (src or {}).items():
            if k not in dst:
                raise ValueError(f"unknown signal weight {k!r}")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"weight for {k!r} must be a finite non-negative number")
            dst[k] = float(v)
    na = set(not_applicable)
    unknown_na = na - set(W) - set(P)
    if unknown_na:
        raise ValueError(f"unknown not_applicable signals {sorted(unknown_na)}")
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise ValueError("entity_id must be a non-empty string")
    if not isinstance(signals, Mapping):
        raise ValueError("signals must be a mapping")

    positive_max = sum(w for k, w in W.items() if k not in na)
    raw_positive = 0.0
    raw_penalties = 0.0
    contributions: list[dict[str, Any]] = []
    missing: list[str] = []

    for path, weight in W.items():
        if path in na:
            continue
        raw = _get_path(signals, path)
        if raw is None:
            missing.append(path)
            value = 0.0
        else:
            value = _validate_signal(path, raw)
        points = value * weight
        raw_positive += points
        contributions.append({
            "signal": path,
            "value": round(value, 4),
            "weight": weight,
            "points": round(points, 3),
            "direction": "supports_priority" if points > 0 else "neutral",
        })

    for path, weight in P.items():
        if path in na:
            continue
        raw = _get_path(signals, path)
        if raw is None:
            missing.append(path)
            value = 0.0
        else:
            value = _validate_signal(path, raw)
        points = value * weight
        raw_penalties += points
        contributions.append({
            "signal": path,
            "value": round(value, 4),
            "weight": -weight,
            "points": round(-points, 3),
            "direction": "reduces_priority" if points > 0 else "neutral",
        })

    # Normalize the positive score to 100, then subtract evidence penalties.
    if positive_max <= 0:
        raise ValueError("at least one positive weight must be > 0")
    score = max(0.0, min(100.0, (raw_positive / positive_max) * 100.0
                         - (raw_penalties / positive_max) * 100.0))
    if score >= 75:
        tier = "high_priority"
    elif score >= 45:
        tier = "medium_priority"
    else:
        tier = "low_priority"

    # Explicitly show data completeness; this is not a confidence probability.
    expected = len([k for k in W if k not in na]) + len([k for k in P if k not in na])
    completeness = (expected - len(missing)) / expected
    strongest = sorted(
        (c for c in contributions if c["points"] != 0),
        key=lambda c: abs(c["points"]),
        reverse=True,
    )[:5]

    return {
        "entity_id": entity_id,
        "priority_score": round(score, 2),
        "priority_tier": tier,
        "score_type": "transparent_priority_score_not_probability",
        "signal_completeness": round(completeness, 3),
        "missing_signals": missing,
        "not_applicable_signals": sorted(na),
        "weights_used": {**{k: v for k, v in W.items() if k not in na}, **{k: -v for k, v in P.items() if k not in na}},
        "top_contributors": strongest,
        "components": contributions,
        "interpretation": (
            "Prioritization aid only. Verify source records and competing "
            "explanations; this score does not prove illicit activity or identity."
        ),
        "method_version": "astra-priority-v1",
    }

def rank_leads(leads: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Score and sort leads descending. Each item needs entity_id and signals."""
    results = []
    for item in leads:
        if not isinstance(item, Mapping):
            raise ValueError("each lead must be a mapping")
        results.append(score_lead(str(item.get("entity_id", "")), item.get("signals", {})))
    return sorted(results, key=lambda x: x["priority_score"], reverse=True)
