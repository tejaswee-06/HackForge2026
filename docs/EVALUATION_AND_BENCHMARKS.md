# Evaluation and benchmarks (reproducible; actual results)

Everything below was executed in this package's build environment (Linux x86_64 container, 2 vCPU,
Python 3.13.16, Node 22.22.0) on 2026-10-17/18 UTC. Re-run the commands to reproduce.

## 1. Test suites
| Suite | Command | Result |
|---|---|---|
| Backend (unit + integration + edge cases) | `cd backend && pytest` | **195 passed** with the optional training deps installed; **194 passed + 1 skipped** (`test_dataset_split`, needs pandas) with only `requirements-dev.txt` — both verified, 0 failed |
| … Person 1 originals (schemas/normalization, provider, graph engine, API) | included | 105 (one superseded 501-stub test replaced, one replay-cutoff test added) |
| … Person 2 originals (evidence, OSINT, risk scoring, temporal, dataset split) | included | 13 |
| … new: DSA 18, OSINT/correlation 8, engine 22, simulation 9, intel API 20 | included | 77 |
| HTTP end-to-end synthetic investigation | `python scripts/e2e_synthetic_investigation.py http://127.0.0.1:8000 --out artifacts/e2e` | **25/25** checks, 0.44 s |
| Person 1 clean-start contract (updated check 10) | `python scripts/clean_start_check.py http://127.0.0.1:8000` | **11/11** |
| Frontend production build | `cd frontend && npm run build` | success (Vite 6, 1 bundle-size warning that also exists in the original) |
| UI interactions on the live stack | `node frontend/e2e/ui-interactions.cjs http://127.0.0.1:4173 out` | **12/12** |
| UI with backend stopped | `node frontend/e2e/ui-backend-down.cjs …` | banner "backend unreachable — showing UI FIXTURE data", footer `UI FIXTURE` |
| Frontend lint / type check / unit tests | – | **NOT CONFIGURED** in the original frontend (plain JSX, no ESLint/TypeScript/test runner); not added to avoid changing the toolchain |

## 2. Labelled synthetic ranking benchmark (`scripts/run_evaluation.py --seeds 1-20`)
Same code path as `GET /api/evaluation`. Raw output: `artifacts/benchmarks/evaluation.json`.

* Scenarios: 60 = 20 incident (92–146 events, 46–100 candidate leads, 39–93 relevant addresses),
  20 benign (57–65 events), 20 insufficient (28 events).
* Labels (from the generator, never given to the engine): relevant = addresses on the generated incident
  splitting tree (+ token-leg recipient); service hubs (exchange deposit hub, DEX-router-like hub) are
  *not* relevant; expected case hypothesis H1/H2/H3 by kind.
* Baseline: time-ignoring BFS reachability from the origin ranked by hop count, then address.

| Metric (denominator) | ASTRA | Baseline |
|---|---|---|
| precision@5 (20 incident scenarios) | 1.000 | 0.940 |
| precision@10 (20 incident scenarios) | 1.000 | 0.915 |
| recall@10 (relevant addresses per scenario) | 0.166 | 0.151 |
| average precision (full ranking) | 0.994 | 0.965 |
| service hub in top 10 (20 incident scenarios) | 0.00 | 0.85 |
| service hub in top 5 | 0.00 | 0.30 |
| case-hypothesis accuracy (60 scenarios) | 1.000 | – |
| benign false-positive rate: leading H1 on benign (20) | 0.000 | – |
| runtime per scenario (mean / max) | 0.025 s / 0.082 s | – |

**Interpretation and limits.** The difference that matters is that ASTRA pushes labelled services
(exchange/DEX hubs reached within 2 hops) down using contradicting service labels, hub shape and
missing incident evidence, while hop distance alone ranks them highly. The absolute numbers are high
because the generator was written by the same team as the engine and encodes the patterns the engine
looks for (rapid forwarding, splitting, source labels): this is a **controlled consistency check, not an
estimate of real-world accuracy**. Recall@10 is low by construction (≫10 relevant addresses per case).
No real labelled incidents were available; Elliptic++ (Bitcoin) metrics previously shown in the UI were
not reproducible from this package and are no longer displayed in API mode.

## 3. Performance (`scripts/benchmark_dsa.py`)
Raw output: `artifacts/benchmarks/dsa.json`; table in `docs/DSA_IMPLEMENTATION.md`. Highlights: index
build 0.29 s and FIFO flow decomposition 0.15 s at 100k edges; full engine 1.29 s for a 21,099-event
simulated investigation (normalization + analysis, single process).

## 4. Reproducibility guarantees (tested)
* Same scenario/config ⇒ byte-identical analysis JSON and identical `snapshot_id` (`test_deterministic_and_reproducible`).
* Same simulation seed/config ⇒ identical raw rows, documents, investigation id and leads.
* Replay at a cutoff is reproducible and excludes all later transfers/documents (6 cutoffs + randomised configs).
* Top-K ties resolved deterministically (tested on shuffled input).

## 5. Sensitivity diagnostics observed (demo scenario)
Removing the forwarding-latency evidence of the top lead (`EV-TEMP-…a002`) re-runs the engine: its score
moves 70.89 → 59.93 and it drops from rank 1 to 2; one claim depended on it. Removing the label-registry
document turns `…a005` from *disputed* into an undisputed lead with a higher score. (`test_engine.py`,
`test_intel_api.py`, UI check #4.)

## 6. Model training (Person 2 Elliptic++ pipeline)
`backend/astra_intel/train.py` is preserved unchanged. **BLOCKED**: the Elliptic++ CSVs (Google Drive)
were not available in the build environment and the dataset is Bitcoin, not Ethereum; no model was
trained, so `model_illicit_score` is reported as *not applicable* and excluded from every score.
