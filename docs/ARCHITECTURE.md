# ASTRA architecture (integrated)

Modular monolith (blueprint §8.1): one FastAPI process, modules separated by ownership, one engine.

```
 React/Vite UI (Person 3, design unchanged)
   src/main.jsx  ── useAstraModel() ── src/services/api.js ── fetch ──┐
   src/services/adapters.js (backend JSON -> existing component shapes)│  same-origin via Vite proxy
                                                                       ▼  (or CORS allow-list)
 FastAPI app  backend/app/main.py
   ├─ app/api/routes.py          Person 1: /health, /api/scenarios, /api/investigations[/{id}], /graph, /paths, /transactions
   ├─ app/api/intel_routes.py    Person 2 engine behind P1 conventions: /analysis /leads /evidence /hypotheses
   │                             /sensitivity /replay /report /flow /route /subgraph /osint[/documents|/collect]
   │                             /api/simulations /api/evaluation /api/investigations (list)
   ├─ /api/intel/*               Person 2 stateless utilities (astra_intel.api router)
   ├─ app/services/ingestion/    Person 1: Etherscan provider, normalizer (validate/normalize/dedupe), scenario loader
   ├─ app/services/graph_engine/ Person 1: NetworkX multigraph export + bounded BFS path tracing (/graph, /paths)
   ├─ app/services/osint_ingest/ Person 1 (source connectivity): fixture loader, GitHub issue search adapter,
   │                             analyst documents, per-investigation source registry (provenance only, no analysis)
   ├─ app/services/analysis.py   glue: InvestigationRecord -> astra_intel.engine.analyze (LRU cache keyed by inputs)
   ├─ app/services/evaluation.py labelled synthetic benchmark (simulation -> P1 normalizer -> engine) vs baseline
   ├─ app/store.py               in-memory investigations (+ OSINT collection, simulation metadata)
   └─ app/schemas/               shared Pydantic contracts (transaction, graph, common envelope, intel)

 Person 2 engine  backend/astra_intel/
   dsa.py          GraphIndex (adjacency lists + hash maps), BFS/DFS, time-respecting min-hop BFS, Dijkstra,
                   heap Top-K, FIFO flow decomposition, Union-Find, metrics
   osint.py        extraction v2 (EVM/tx-hash/shortened/BTC), EIP-55 validation (keccak.py), sentence stance
   correlation.py  match grades, freshness, contradictions, Union-Find independence groups
   risk_scoring.py transparent priority score (astra-priority-v1; + weight overrides, not-applicable signals)
   evidence.py / falsification.py   P2 heuristic hypothesis score + remove/downgrade recomputation
   engine.py       analyze(): the single pipeline (features, evidence, claims, hypotheses, timeline, boundaries)
   report.py       casebook JSON + escaped HTML built only from an analysis
   simulation.py   deterministic labelled scenario generator (raw rows + documents + ground truth)
   train.py/dataset.py/features.py  original Elliptic++ training pipeline (separate, optional, not run)
```

## Request flow (example: Evidence → toggle clue)
1. UI `toggleClue(false)` → `POST /api/investigations/{id}/sensitivity {remove_evidence_ids:[EV-TEMP-…]}`.
2. `intel_routes.post_sensitivity` validates ids → `AnalysisService.run(rec, cfg)` (baseline, cached) and
   `run(rec, cfg, overrides)` (counterfactual) → both call `astra_intel.engine.analyze` on the same
   canonical `NormalizedTransfer`s and `SourceDocument`s.
3. `engine.compare_runs` → rank/score deltas, affected claims, hypothesis changes → UI shows them.

## Engine pipeline (`astra_intel.engine.analyze`)
1. Filter records by availability (transfers: event time ≤ cutoff; documents: policy time ≤ cutoff).
2. `GraphIndex` (adjacency/hash maps) → DFS time-respecting reachability → layered-BFS min hops →
   FIFO flow decomposition per asset → Union-Find weak components → degree percentiles.
3. OSINT: extract + validate indicators → correlate with graph → contradictions → independence groups.
4. Candidates = reachable nodes ∪ OSINT-matched nodes (bounded) → feature vector → `score_lead`.
5. Heap Top-K ranking (deterministic ties) → Dijkstra fastest route for top-k leads.
6. Evidence items (Observed/Calculated/Source-reported/Disputed/Unresolved) → claims (bidirectional links)
   → H1/H2/H3 per lead (+case = top lead with origin evidence) → single-evidence falsification checks.
7. Timeline (event vs retrieval time), unresolved boundaries, limitations, metadata (snapshot id, config hash).

## Data mode semantics
`data_mode ∈ {synthetic, real, cached}` on every record, source and response; the UI shows
`data_label ∈ {SYNTHETIC / SIMULATED, LIVE, CACHED, UI FIXTURE}`. Live OSINT collection is refused (409)
on synthetic investigations; wallet queries never fall back to synthetic data; analyst documents inherit the
investigation's mode.

## Persistence
In-memory (`InvestigationStore`, bounded by `ASTRA_MAX_INVESTIGATIONS`). Scenario cases are rebuilt at
startup; simulations are reproducible from their seed/config. No database or migrations exist (documented
as PARTIAL in REMAINING_WORK.md).
