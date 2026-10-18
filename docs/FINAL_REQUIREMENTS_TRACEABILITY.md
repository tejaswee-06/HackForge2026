# Final requirements traceability

Sources: **WD** = ASTRA_Work_Division_DSA_OSINT_Corrected.pdf; **BP** = ASTRA Ghost Attacks Project
Blueprint (section numbers); **MP** = master execution prompt (section numbers).
Status: COMPLETE (implemented + tested), PARTIAL, MISSING, BLOCKED (external dependency), UNVERIFIED.

## Person 1 — data, graph, API
| Requirement | Source | Implementation | Evidence (tests/runs) | Status |
|---|---|---|---|---|
| Canonical transfer schema, exact amounts, unknown≠0, UTC | WD P1, BP 7.1/7.4 | `app/schemas/transaction.py` | test_schemas_normalization (29) | COMPLETE |
| Normalization + dedupe + malformed-row reporting | WD P1, MP 5 | `services/ingestion/normalizer.py` | tests; e2e step 3 | COMPLETE |
| Etherscan adapter: timeout, retries, rate limit, cache, no silent fallback | WD P1, BP 9.1, MP 5 | `services/ingestion/providers.py` | test_provider (21, mocked HTTP) | COMPLETE (mocked) / **UNVERIFIED live** |
| Directed time-aware multigraph + bounded path tracing | WD P1, BP 7.1 | `services/graph_engine/engine.py` | test_graph_engine (19), clean-start check | COMPLETE |
| Shared Pydantic schemas incl. OSINT/sensitivity/simulation | WD P1, MP 4 | `app/schemas/*.py` | API tests | COMPLETE |
| Health, scenarios, investigations, graph/paths/transactions endpoints | BP 8.3 | `app/api/routes.py` | test_api (36), clean_start_check 11/11 | COMPLETE |
| Connect permitted threat feeds / developer-repository sources | WD P1 | `services/osint_ingest/service.py` (fixtures, GitHub issue search, analyst docs) | test_intel_api (collect success/rate-limit/timeout/malformed/disabled) | COMPLETE (mocked) / **UNVERIFIED live** |
| Source provenance (URL, retrieval time, type, raw ref, content hash) | WD P1 | `SourceDocument` | test_intel_api, test_engine | COMPLETE |
| Error envelope, validation, 404-before-validation | BP 13.3 | `main.py`, routes | test_api, test_intel_api | COMPLETE |
| Database persistence / investigation storage | BP 8, MP 5 | in-memory store only | – | PARTIAL (no DB) |
| Exhaustive historical windows for live wallets | MP 5 | post-fetch filter + explicit limitation | test_api | PARTIAL (documented) |

## Person 2 — OSINT, DSA, intelligence
| Requirement | Source | Implementation | Evidence | Status |
|---|---|---|---|---|
| Indicator extraction + validation (addresses, tx hashes, aliases/incident refs, artifacts) | WD P2, MP 6.2 | `astra_intel/osint.py` (+EIP-55 `keccak.py`) | test_osint_correlation | COMPLETE for addresses/tx hashes/shortened forms; aliases & repository artifacts PARTIAL (stance terms only, no alias entity model) |
| Exact / strong / weak / ambiguous / no-match / contradictory | WD P2, MP 6.2 | `correlation.py` | test_osint_correlation, e2e step 6–7 | COMPLETE |
| On-chain ↔ OSINT correlation with explanation, direct/indirect, freshness, unknowns | MP 6.3 | `correlation.py`, `engine._osint_evidence` | tests | COMPLETE |
| Provenance through every transformation; three clocks kept separate | MP 6.4, BP 7.4 | finding/evidence provenance; timeline `event_time`/`retrieved_at` | test_engine, replay tests | COMPLETE |
| Independent corroboration (no double counting of mirrors) | BP 11 | Union-Find `independence_groups` | test_engine (mirror), test_osint_correlation | COMPLETE |
| Adjacency lists / hash maps | WD DSA | `dsa.GraphIndex` | test_dsa | COMPLETE |
| BFS / DFS bounded | WD DSA | `bfs_neighborhood`, `time_respecting_reachability`, `has_directed_cycle` | test_dsa | COMPLETE |
| Shortest paths (BFS hops; Dijkstra with documented non-negative cost) | WD DSA | `min_hop_time_respecting`, `bfs_min_hops_static`, `dijkstra_fastest_route` | test_dsa, `/route` tests | COMPLETE |
| Top-K heap, deterministic ties | WD DSA, BP 13.4 | `dsa.top_k` | test_dsa | COMPLETE |
| Flow decomposition with tx refs + residuals | WD DSA | `dsa.decompose_flow` | test_dsa (conservation, residuals, cycles, assets) | COMPLETE |
| Union-Find only for symmetric grouping | WD DSA | weak components, source independence | test_dsa | COMPLETE |
| DSA powers real features (no DSA page) | WD | engine + routes; UI uses results | ui checks | COMPLETE |
| Temporal features & graph metrics | WD P2, BP 7.2 | `engine._features` (+ P2 `temporal.py` stateless) | test_engine | COMPLETE |
| Transparent ranking, explanations, missing ≠ negative, not a probability | WD P2, BP 7.3, MP 8 | `risk_scoring.py` + engine | test_engine, test_risk_scoring | COMPLETE |
| Claim-and-evidence graph, bidirectional | BP 4 (USP 5), 7.4 | `engine._claims` + back-links | test_engine | COMPLETE |
| H1 / H2 / H3 competing hypotheses, H3 legitimate | BP 4, MP 9 | `engine._hypotheses` | test_simulation (kinds), test_engine | COMPLETE |
| Sensitivity (remove/downgrade evidence, remove document, change weights) via real engine | BP 7.5, MP 9 | `POST /sensitivity`, `Overrides` | test_engine, test_intel_api, UI #4 | COMPLETE |
| Falsification checks & "what would weaken" | MP 9 | `engine._falsification` | test_engine/UI | COMPLETE |
| Historical replay with cutoff, policy, no leakage, reproducible | BP 4 (USP 8), MP 10 | `analyze(cutoff)`, `/replay` | parametrised leakage tests, randomised test, e2e step 9, UI #6 | COMPLETE |
| Report export matching UI (JSON/HTML), version/cutoff/limitations | BP 4 (USP 12), MP 10 | `report.py`, `/report` | test_engine, test_intel_api, e2e step 10, UI #8 | COMPLETE (PDF via browser print) |
| Labelled scenarios + reproducible benchmark vs baseline | WD P2, BP 12 | `simulation.py`, `services/evaluation.py`, `scripts/run_evaluation.py` | test_simulation; 60-scenario run | COMPLETE (synthetic only) |
| Benchmark on real labelled incidents / bridge & mixer scenarios | BP 12.1 | – | – | MISSING (no real labels; no bridge mapping) |
| Trained model (Elliptic++) | P2 package | `train.py` unchanged | – | BLOCKED (dataset unavailable; Bitcoin ≠ Ethereum) |
| Cash-Out Interception Radar (stretch) | BP 4 USP 4 | lead `next_check` only | – | MISSING (stretch) |

## Person 3 — frontend and integration
| Requirement | Source | Implementation | Evidence | Status |
|---|---|---|---|---|
| Preserve existing UI (layout, routes, components, styles, animations) | MP 3 | `main.jsx` data bindings only; 3 additive CSS rules | baseline vs after screenshots + inventory (10 pages, same headings/panels) | COMPLETE |
| Wire to backend via existing adapter conventions | WD P3, MP 13 | `services/api.js`, `services/adapters.js`, Vite proxy | UI #1–#11 | COMPLETE |
| Graph from backend graph data | MP 13.3 | `/graph` + `buildGraphView` | UI #3 | COMPLETE |
| Loading / error / empty / backend-unreachable states | WD P3, MP 13.11 | status banners (existing `hypothesis-note` style), empty rows | ui-backend-down | COMPLETE |
| Show OSINT provenance, contradictions, unresolved status; no UI-side attribution | WD P3 | Sources, Evidence, Graph inspector | UI checks | COMPLETE |
| Visible synthetic/live labels | WD acceptance, MP 12 | header, footer, chips, exports | UI #1b | COMPLETE |
| Mode switching (scenario / simulation / live wallet) | MP 11–12 | Settings → Investigation source | UI #9–#11 | COMPLETE |
| React **TypeScript** screens | WD P3, BP 9 | original frontend is JSX | – | PARTIAL (not converted; out of scope of "do not rewrite") |
| Separate routes per page (`/cases/:id/...`) | BP 5 | original single-page state navigation kept | – | PARTIAL (preserved as designed) |
| Lint / type-check / frontend unit tests | MP 14.E | none configured originally | build only | BLOCKED/NOT CONFIGURED |

## Cross-cutting
| Requirement | Source | Evidence | Status |
|---|---|---|---|
| End-to-end synthetic investigation through shared contracts | WD acceptance, MP 14.D | e2e 25/25 + UI 12/12 | COMPLETE |
| Secrets server-side, no secrets in package | BP 11, MP 15 | secret scan; `.env` excluded | COMPLETE (rotate leaked key) |
| Security review (validation, bounds, XSS, CORS, logging) | MP 15 | SECURITY_AND_CONFIGURATION.md, tests | COMPLETE |
| Live provider verification | MP 5 | – | BLOCKED (no credentials) |
| Benchmarks with environment details | MP 14.F | artifacts/benchmarks/*.json | COMPLETE |
| macOS/Linux startup from a clean ZIP extraction | MP 18 | start-*.sh → tests, e2e 25/25, UI 12/12 on the extracted copy | COMPLETE |
| Windows PowerShell startup | MP 18 | start-*.ps1 | UNVERIFIED (no PowerShell in build environment) |
