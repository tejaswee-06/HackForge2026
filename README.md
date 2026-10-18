# ASTRA — Quantitative OSINT Blockchain Intelligence Engine (integrated)

**Follow the flow. Verify the claim.** ASTRA fuses a time-aware blockchain transaction graph with
source-backed public intelligence to produce explainable, auditable *candidate* leads — never claims of
identity, ownership or guilt. Scores are transparent prioritisation aids (0–100), not probabilities.

This package integrates the three workstreams into one application:

| Workstream | Where | What it does |
|---|---|---|
| **Person 1** — data & graph backend | `backend/app/` | Canonical transfer schema, Etherscan adapter (timeouts/retries/rate limits/cache, no silent fallback), normalization + dedupe, directed multigraph, bounded path tracing, FastAPI app, OSINT *source connectivity* (fixture loader, GitHub issue-search adapter, analyst documents) |
| **Person 2** — OSINT, DSA & intelligence engine | `backend/astra_intel/` | Indicator extraction/validation (EIP-55), entity matching (exact/strong/weak/ambiguous), OSINT↔on-chain correlation, Union-Find source independence, freshness, DSA (adjacency/hash indexes, BFS/DFS, time-respecting shortest paths, Dijkstra, heap Top-K, flow decomposition, Union-Find), feature pipeline + explainable ranking, claim/evidence graph, H1/H2/H3 hypotheses, falsification, sensitivity, replay, report, deterministic simulation, evaluation |
| **Person 3** — frontend | `frontend/` | The existing React/Vite console (design preserved), wired to the backend through `src/services/api.js` + `src/services/adapters.js` |

Everything the UI shows comes from **one engine call** (`astra_intel.engine.analyze`) over Person 1's
canonical records; sensitivity, replay and reports re-run that same function.

## Prerequisites
* Python **3.10+** (tested with 3.13.16) · Node.js **18+** (tested with 22.22) · npm
* No API keys are needed for the offline synthetic demo.

## Quick start — macOS / Linux
```bash
# terminal 1: backend on http://127.0.0.1:8000 (API docs at /docs)
./start-backend.sh            # first run creates backend/.venv and installs requirements-dev.txt
# terminal 2: frontend on http://localhost:5173 (proxies /api and /health to the backend)
./start-frontend.sh           # first run runs npm install
```
Manual equivalent:
```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 194 passed, 1 skipped (195 with intel_training deps)
uvicorn app.main:app --port 8000         # add --env-file ../.env for live providers
cd ../frontend && npm install && npm run dev
```

## Quick start — Windows PowerShell
```powershell
# terminal 1 (backend)
.\start-backend.ps1
# terminal 2 (frontend)
.\start-frontend.ps1
```
Manual equivalent:
```powershell
cd backend
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --port 8000
# new terminal
cd frontend; npm install; npm run dev
```
If script execution is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

**Startup order:** backend first, then frontend. No database or migration step: the MVP keeps
investigations in memory (the demo scenario is re-created deterministically at startup).

## Using it
1. Open http://localhost:5173 → *Enter workspace*. The demo case `inv-ghost_attacks_synthetic_v1`
   (Person 1's deterministic scenario + labelled synthetic OSINT documents) loads automatically and is
   labelled **SYNTHETIC / SIMULATED** in the header, footer, every data-mode chip and every export.
2. **Settings → Investigation source** switches data mode:
   *Demo scenario*, *Simulation* (seed + kind: `incident` / `benign` / `insufficient`) or *Live wallet*
   (needs `ETHERSCAN_API_KEY`; without it you get an explicit `PROVIDER_NOT_CONFIGURED` error — never
   synthetic data).
3. Graph → Leads → Evidence (toggle the clue to re-run the engine) → Hypotheses → Timeline (cutoff slider
   re-runs the engine with only records known at the cutoff) → Casebook (JSON / printable HTML).

### Synthetic demo without the UI
```bash
uvicorn app.main:app --app-dir backend --port 8000 &
python scripts/e2e_synthetic_investigation.py http://127.0.0.1:8000 --out artifacts/e2e   # 25 checks
python scripts/clean_start_check.py http://127.0.0.1:8000                                 # Person 1 contract
```

## Configuration
Copy `.env.example` to `.env` (never commit it). All variables are optional; see
`docs/SECURITY_AND_CONFIGURATION.md`. Frontend options are in `frontend/.env.example`
(`VITE_ASTRA_DATA_SOURCE=api|fixtures`; never put secrets in `VITE_*`).

## Verification (actually executed — see docs/FINAL_IMPLEMENTATION_REPORT.md)
| Check | Command | Result |
|---|---|---|
| Backend unit + integration | `cd backend && pytest` | 194 passed + 1 skipped (pandas-only test); 195 with training deps |
| HTTP end-to-end synthetic investigation | `scripts/e2e_synthetic_investigation.py` | 25/25 |
| Person 1 clean-start contract | `scripts/clean_start_check.py` | 11/11 |
| Frontend production build | `cd frontend && npm run build` | success |
| UI interactions (Playwright, live stack) | `frontend/e2e/ui-interactions.cjs` | 12/12 |
| Labelled synthetic evaluation | `scripts/run_evaluation.py --seeds 1-20` | measured, see docs |
| DSA / engine performance | `scripts/benchmark_dsa.py` | measured, see docs |

## Documentation
`docs/ARCHITECTURE.md` · `docs/API_CONTRACTS.md` · `docs/DSA_IMPLEMENTATION.md` · `docs/OSINT_PIPELINE.md` ·
`docs/SIMULATION_AND_LIVE_MODES.md` · `docs/EVALUATION_AND_BENCHMARKS.md` · `docs/SECURITY_AND_CONFIGURATION.md` ·
`docs/FINAL_REQUIREMENTS_TRACEABILITY.md` · `docs/FINAL_IMPLEMENTATION_REPORT.md` · `docs/REMAINING_WORK.md`.
Original handover docs: `docs/person1/`, `docs/person2/`, `frontend/README.md`.

## Honest status
Implemented and tested end to end on synthetic data. **Not verified against live networks:** the
Etherscan adapter and the GitHub OSINT adapter are tested with mocked HTTP only (no credentials were
available). The Elliptic++ training pipeline in `backend/astra_intel/train.py` is unchanged and was not
run (dataset not available). Evaluation numbers come from a synthetic generator written alongside the
engine and do not estimate real-world accuracy. See `docs/REMAINING_WORK.md`.
