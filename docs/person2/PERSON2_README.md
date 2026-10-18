> **Integration note (final package):** the package now lives in `backend/astra_intel/`. Its stateless API is mounted at `/api/intel/*` of the integrated backend, and the investigation workflow uses `astra_intel.engine` (see `docs/ARCHITECTURE.md`). Training scripts are in `intel_training/`; run them from `backend/`.

# ASTRA — Person 2: Quantitative Intelligence & OSINT Engine

This package implements Person 2's workstream for ASTRA:
- Elliptic++ transaction dataset ingestion and schema validation
- Chronological train/validation/test splitting using the dataset's time-step field
- Supervised illicit-vs-licit transaction classification (unknown labels excluded)
- Graph-derived transaction features from the transaction edge list
- Evaluation metrics and model artifact export
- Explainable evidence scoring, competing hypotheses, and evidence sensitivity/falsification
- A small FastAPI interface for integration with Person 1 (backend) and Person 3 (frontend)

**Dataset source:** official Elliptic++ repository and its linked Google Drive folder:
- https://github.com/git-disl/EllipticPlusPlus
- https://drive.google.com/drive/folders/1MRPXz79Lu_JGLlJ21MDfML44dKN9R08l?usp=sharing

The official dataset contains transaction and wallet/actor graph data. This package's initial training pipeline uses the **transaction-level** files:
- `txs_features.csv`
- `txs_classes.csv`
- `txs_edgelist.csv`

The official authors report 203,769 transaction nodes, 234,355 transaction-flow edges, 49 time steps and 183 features. Class conventions should be confirmed against the downloaded file/readme; the loader supports common label variants and prints the observed label counts. Unknown/unlabelled rows are excluded from supervised training, not silently relabelled as licit.

## 1. Install

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Download and place the real dataset

Download Elliptic++ from the official source above. Extract the transaction CSV files into:

```text
data/ellipticpp/txs_features.csv
data/ellipticpp/txs_classes.csv
data/ellipticpp/txs_edgelist.csv
```

Do not commit the dataset to GitHub unless its licence/terms permit redistribution. The `.gitignore` excludes local data and trained models.

## 3. Train and evaluate

```bash
python -m astra_intel.train --data-dir data/ellipticpp --output-dir artifacts
```

The script:
1. Detects likely ID, time-step, and label columns.
2. Joins labels and graph-derived in/out degree features.
3. Excludes unknown labels from supervised training.
4. Uses chronological time-step partitions (earlier steps for training, later steps for validation/test).
5. Trains XGBoost when installed; otherwise uses scikit-learn HistGradientBoosting.
6. Writes metrics, class counts, predictions, feature importance when supported, and the model to `artifacts/`.

**Important:** temporal splitting reduces one form of leakage but does not guarantee a leakage-free benchmark. Audit all source features and graph feature construction before making scientific claims. Do not tune on the test set.

## 4. Start the integration API

```bash
uvicorn astra_intel.api:app --reload
```

- `GET /health`
- `POST /score/evidence`
- `POST /hypotheses/compare`
- `POST /falsify`
- `GET /model/info`

The API uses documented, deterministic heuristic evidence scoring for case-level hypothesis comparison. It does not pretend the classifier's probability is proof of criminality. The trained classifier integration is a separate layer and can be wired in after a model artifact exists.

## 5. Example evidence payload

```json
{
  "case_id": "ASTRA-DEMO-001",
  "hypothesis_id": "H1",
  "evidence": [
    {
      "evidence_id": "E1",
      "kind": "on_chain_flow",
      "direction": "supports",
      "reliability": 0.9,
      "strength": 0.8,
      "source": "public_chain_record",
      "description": "Observed transaction path in the supplied dataset"
    },
    {
      "evidence_id": "E2",
      "kind": "public_report",
      "direction": "contradicts",
      "reliability": 0.7,
      "strength": 0.4,
      "source": "https://example.org/report",
      "description": "A source that conflicts with the hypothesis"
    }
  ]
}
```

## 6. Person 2 deliverables and integration contract

- Keep shared schemas stable and coordinate with Person 1 before renaming fields.
- `astra_intel/features.py`: graph and tabular feature preparation.
- `astra_intel/train.py`: reproducible model training/evaluation.
- `astra_intel/evidence.py`: evidence provenance and scoring.
- `astra_intel/falsification.py`: remove/downgrade evidence and recompute scores.
- `astra_intel/api.py`: integration endpoints.
- `artifacts/metrics.json`: model evaluation summary.
- `artifacts/predictions.csv`: holdout predictions.
- `artifacts/feature_importance.csv`: feature importance where available.

## 7. Important limitations

- Elliptic++ is Bitcoin data, not Ethereum-specific DeFi exploit data. It is useful for developing/evaluating transaction classification and graph analysis, but must not be represented as Ethereum validation.
- The model detects patterns correlated with dataset labels; it does not identify real-world people or establish guilt.
- Dataset labels are incomplete. Unknown examples are not treated as negative.
- Public OSINT ingestion must respect terms of service, access restrictions, rate limits, and privacy boundaries.
- No actual training is claimed until the CSVs are downloaded and the training command completes successfully.
- Check the official dataset licence, paper citation requirements, and redistribution terms before publication.

## Citation

Youssef Elmougy and Ling Liu. “Demystifying Fraudulent Transactions and Illicit Nodes in the Bitcoin Network for Financial Forensics.” KDD 2023. https://doi.org/10.1145/3580305.3599803

## Person 2 complete package

See [`docs/PERSON2_DELIVERABLES.md`](docs/PERSON2_DELIVERABLES.md) for the combined quantitative, evidence, falsification, risk-scoring, testing, and integration workflow.

## Additional Person 2 intelligence modules

### Explainable lead priority scoring
`astra_intel/risk_scoring.py` returns a transparent 0–100 priority score, priority tier, missing inputs, and top contributors. It is a prioritization heuristic—not a probability or identity attribution. Its initial weights must be evaluated against labeled scenarios.

### Temporal transaction fingerprinting
`astra_intel/temporal.py` computes inter-event time deltas, a configurable rapid-event ratio, fan-out/fan-in proxies, and per-asset amount summaries. It does not sum unlike assets and does not infer coordination from timing alone.

API: `POST /features/temporal` with `{"transfers":[{"source":"A","target":"B","timestamp":100,"amount":1,"asset":"BTC"}]}`.

### Evidence-backed OSINT indicator extraction
`astra_intel/osint.py` extracts candidate Bitcoin/EVM address strings and URLs from text supplied by a permitted source. It retains source URL, retrieval time, and quote context. These are unverified candidates; the module does not scrape the web or claim wallet ownership.

API: `POST /osint/extract`, then optionally `POST /osint/match` for exact-string comparison.

### Lead scoring API
- `POST /score/lead` — score one lead
- `POST /score/rank` — rank multiple leads
- `POST /score/evidence` — score a hypothesis from support/contradiction evidence
- `POST /hypotheses/compare` — compare competing hypotheses
- `POST /falsify` — remove/downgrade selected evidence and recompute

### Synthetic benchmark smoke test
Run `python -m scripts.benchmark_scenarios`. It writes `artifacts_person2/synthetic_benchmark.json`. This is only a deterministic plumbing test on tiny hand-authored fixtures—not a real-world performance benchmark.

## Verification status
The unit tests exercise schemas, scoring behavior, temporal feature extraction, and OSINT provenance handling. A real benchmark still requires labeled benign and suspicious cases, confirmed source provenance, feature-leakage review, and integration against Person 1's actual schema. Do not fabricate live OSINT, wallet attribution, or Ethereum/DeFi validation from Elliptic++ Bitcoin data.
