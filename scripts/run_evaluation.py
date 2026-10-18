#!/usr/bin/env python3
"""Run the labelled synthetic benchmark (ASTRA vs BFS baseline) and save the measured results.

    python scripts/run_evaluation.py [--seeds 1-20] [--out artifacts/benchmarks/evaluation.json]

Same code path as GET /api/evaluation (app.services.evaluation.run_benchmark).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.evaluation import run_benchmark  # noqa: E402

spec = sys.argv[sys.argv.index("--seeds") + 1] if "--seeds" in sys.argv else "1-20"
a, _, b = spec.partition("-")
seeds = list(range(int(a), int(b) + 1)) if b else [int(x) for x in spec.split(",")]
out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "artifacts" / "benchmarks" / "evaluation.json"
res = run_benchmark(seeds, ks=(5, 10))
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(res, indent=2))
print(json.dumps(res["aggregate"], indent=2))
print(res["summary"])
print("wrote", out)
