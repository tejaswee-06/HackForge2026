"""Run a small deterministic unit benchmark for ASTRA lead ranking.

These are synthetic test fixtures, not real-world validation. Output reports
ranking quality on the hand-authored fixtures only.
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / 'backend'))  # integrated layout

import json
from pathlib import Path
from astra_intel.risk_scoring import rank_leads

CASES = [
    {"entity_id":"fixture-high-1","label":1,"signals":{"model_illicit_score":0.95,"temporal":{"rapid_forwarding":0.9,"fragmentation":0.8},"graph":{"incident_path_proximity":1.0,"flow_continuity":0.8},"osint":{"source_quality":0.9,"indicator_match":1.0,"independent_corrob":0.8}}},
    {"entity_id":"fixture-benign-1","label":0,"signals":{"model_illicit_score":0.1,"temporal":{"rapid_forwarding":0.2},"graph":{"incident_path_proximity":0.0},"osint":{"indicator_match":0.0}}},
    {"entity_id":"fixture-high-2","label":1,"signals":{"model_illicit_score":0.8,"graph":{"incident_path_proximity":0.8,"flow_continuity":0.7},"evidence":{"contradiction_strength":0.1}}},
    {"entity_id":"fixture-benign-2","label":0,"signals":{"model_illicit_score":0.2,"graph":{"unusual_degree":0.3},"evidence":{"contradiction_strength":0.8}}},
]

def main():
    ranked = rank_leads(CASES)
    rank_by_id = {x["entity_id"]: i+1 for i,x in enumerate(ranked)}
    positives = [x for x in CASES if x["label"] == 1]
    precision_at_2 = sum(1 for x in ranked[:2] if x["entity_id"] in {p["entity_id"] for p in positives}) / 2
    result = {"benchmark_type":"synthetic_smoke_test_not_real_world_validation",
              "case_count":len(CASES), "ranked_leads":[{"entity_id":x["entity_id"],"priority_score":x["priority_score"],"rank":rank_by_id[x["entity_id"]],"label_for_fixture_only":next(c["label"] for c in CASES if c["entity_id"]==x["entity_id"])} for x in ranked],
              "precision_at_2_on_synthetic_fixtures":precision_at_2,
              "limitations":["Tiny hand-authored synthetic set", "Not a model performance estimate", "Weights need evaluation on labelled real or appropriately governed data"]}
    out = Path("artifacts_person2")
    out.mkdir(exist_ok=True)
    (out/"synthetic_benchmark.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
if __name__ == "__main__": main()
