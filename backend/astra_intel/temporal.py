"""Deterministic temporal transaction fingerprinting from observed transfer events.

Input rows must contain source, target, timestamp and optionally amount/asset.
Features are descriptive signals, not proof of coordination or wrongdoing.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping
import math


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise ValueError(f"Unsupported timestamp: {value!r}") from exc
    raise ValueError(f"Unsupported timestamp: {value!r}")


def temporal_fingerprint(transfers: Iterable[Mapping[str, Any]], *, rapid_window_seconds: float = 600.0) -> dict[str, Any]:
    """Summarize timing/flow for an iterable of normalized transfers.

    Required keys: source, target, timestamp. Optional: amount, asset, tx_hash.
    Amounts are only aggregated within the same asset label; no cross-token sums.
    """
    if rapid_window_seconds <= 0:
        raise ValueError("rapid_window_seconds must be positive")
    rows = []
    for i, item in enumerate(transfers):
        if not isinstance(item, Mapping):
            raise ValueError(f"transfer {i} must be a mapping")
        for key in ("source", "target", "timestamp"):
            if key not in item or item[key] is None or str(item[key]).strip() == "":
                raise ValueError(f"transfer {i} missing required field {key!r}")
        ts = _timestamp(item["timestamp"])
        amount = item.get("amount")
        if amount is not None:
            try:
                amount = float(amount)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"transfer {i} amount must be numeric") from exc
            if not math.isfinite(amount) or amount < 0:
                raise ValueError(f"transfer {i} amount must be finite and non-negative")
        rows.append({**item, "_ts": ts, "_amount": amount})
    rows.sort(key=lambda r: r["_ts"])
    if not rows:
        return {"transfer_count": 0, "unique_sources": 0, "unique_targets": 0,
                "inter_event_delta_seconds": [], "median_inter_event_delta_seconds": None,
                "rapid_forwarding_ratio": 0.0, "fragmentation_ratio": 0.0,
                "consolidation_ratio": 0.0, "flow_by_asset": {},
                "data_quality": {"status": "empty", "warnings": ["No transfers supplied"]}}
    deltas = [max(0.0, rows[i]["_ts"] - rows[i-1]["_ts"]) for i in range(1, len(rows))]
    sorted_deltas = sorted(deltas)
    median = None if not sorted_deltas else (sorted_deltas[(len(sorted_deltas)-1)//2] if len(sorted_deltas)%2 else (sorted_deltas[len(sorted_deltas)//2-1]+sorted_deltas[len(sorted_deltas)//2])/2)
    outgoing, incoming = defaultdict(int), defaultdict(int)
    flow = defaultdict(lambda: {"transfer_count": 0, "amount_total": 0.0, "amount_observed_count": 0})
    for r in rows:
        outgoing[str(r["source"])] += 1
        incoming[str(r["target"])] += 1
        asset = str(r.get("asset") or "UNSPECIFIED")
        flow[asset]["transfer_count"] += 1
        if r["_amount"] is not None:
            flow[asset]["amount_total"] += r["_amount"]
            flow[asset]["amount_observed_count"] += 1
    # Ratios are descriptive proxy features; they are not learned probabilities.
    rapid = sum(d <= rapid_window_seconds for d in deltas) / len(deltas) if deltas else 0.0
    fragmentation = sum(1 for count in outgoing.values() if count >= 3) / len(outgoing) if outgoing else 0.0
    consolidation = sum(1 for count in incoming.values() if count >= 3) / len(incoming) if incoming else 0.0
    warnings = []
    if any(r.get("asset") is None for r in rows): warnings.append("Some assets are unspecified; amounts were not combined across assets")
    if any(r.get("amount") is None for r in rows): warnings.append("Some transfer amounts are unavailable")
    return {
        "transfer_count": len(rows), "unique_sources": len(outgoing), "unique_targets": len(incoming),
        "inter_event_delta_seconds": [round(x, 3) for x in deltas],
        "median_inter_event_delta_seconds": None if median is None else round(median, 3),
        "rapid_forwarding_ratio": round(rapid, 4),
        "fragmentation_ratio": round(fragmentation, 4),
        "consolidation_ratio": round(consolidation, 4),
        "flow_by_asset": {k: {"transfer_count": v["transfer_count"], "amount_total": round(v["amount_total"], 12), "amount_observed_count": v["amount_observed_count"]} for k,v in flow.items()},
        "data_quality": {"status": "ok", "warnings": warnings},
        "interpretation": "Descriptive transaction timing and fan-out/fan-in signals; not proof of coordination or illicit activity."
    }
