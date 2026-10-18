# ASTRA — Person 2 Deliverables & Integration Guide

## Included in this package

1. **Transaction classification experiment** — `astra_intel/train.py`, `dataset.py`, `features.py`.
   The main training script uses the full-feature pipeline and chronological split.
2. **Explainable lead priority scoring** — `astra_intel/risk_scoring.py`. Returns a transparent 0–100 priority score, tier, components, missing inputs, and top contributors.
3. **Evidence and falsification utilities** — `astra_intel/evidence.py`, `astra_intel/falsification.py`.
4. **Temporal transaction fingerprinting** — `astra_intel/temporal.py` for inter-event deltas, rapid-event ratios, fan-out/fan-in proxies, and per-asset amount summaries.
5. **OSINT indicator extraction** — `astra_intel/osint.py` extracts candidate addresses and URLs from permitted/source-supplied text and preserves provenance; extracted indicators remain unverified.
6. **API / integration surface** — `astra_intel/api.py` and `docs/INTEGRATION_CONTRACT.md`.
7. **Tests** — dataset split, evidence, risk scoring, temporal fingerprints, and OSINT extraction.
8. **Synthetic smoke benchmark** — `scripts/benchmark_scenarios.py`; not real-world validation.
9. **Examples and operational documentation** — `examples/`, `README.md`, and model card template.

## Run from the project root (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
```

If PowerShell blocks activation, run the Python commands using `.venv\Scripts\python.exe` directly.

## Train the full-feature model

Ensure the Elliptic++ files exist in `data\ellipticpp\`:
- `txs_features.csv`
- `txs_classes.csv`
- `txs_edgelist.csv`

Then run:

```powershell
python -m astra_intel.train --data-dir data/ellipticpp --output-dir artifacts
```

The dataset is not bundled in this ZIP. Use the dataset source/permissions documented by the team. The data is Bitcoin transaction data and must not be described as direct validation on Ethereum/DeFi exploit cases.

## Risk scoring usage

```python
from astra_intel.risk_scoring import score_lead

result = score_lead("wallet-or-lead-id", {
    "model_illicit_score": 0.72,
    "temporal": {"rapid_forwarding": 0.8, "fragmentation": 0.5},
    "graph": {"incident_path_proximity": 1.0, "flow_continuity": 0.7},
    "osint": {
        "source_quality": 0.9,
        "independent_corrob": 0.5,
        "indicator_match": 1.0,
        "source_freshness": 0.8
    },
    "evidence": {"contradiction_strength": 0.2, "missing_link_strength": 0.1}
})
```

All inputs to the scoring function must be normalized to `[0, 1]` by the upstream calculation and backed by observable evidence. Missing signals are treated as zero and listed explicitly. The score is a prioritization heuristic, **not a calibrated probability** and not proof of criminality or real-world identity. The included weights are initial heuristics and require evaluation on labelled scenarios before any claim of validation.

## Remaining integration work

- Map Person 1's actual normalized graph/API schema into the scoring signal fields.
- Wire Person 1's real transfer rows into temporal fingerprinting and the UI/API.
- Select a permitted OSINT source adapter and add retrieval, content hashing, rate-limit handling, and source verification before production use.
- Implement or connect the data-backed temporal signals and OSINT source adapters; do not populate these fields with invented values.
- Run tests, benchmark against labelled benign and suspicious scenarios, record actual metrics, and update the model card.
- Ensure UI labels simulated, cached, and live data distinctly; preserve source URLs, retrieval timestamps, and contradictory evidence.
