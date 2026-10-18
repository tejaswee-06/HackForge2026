# ASTRA API contracts (as implemented)

Base URL `http://127.0.0.1:8000`; interactive schema at `/docs` and `/openapi.json`. JSON, UTC ISO-8601
timestamps (timezone required on input), exact integer token amounts as strings, `null` = unknown (never 0).

## Shared response metadata
Every investigation-scoped response includes `investigation_id, data_mode (synthetic|real|cached),
analysis_version, generated_at, data_cutoff, limitations[], sources[]`. Analysis-derived responses add
`data_label, synthetic, snapshot_id, config_hash, engine_version`.

## Error envelope (all errors)
```json
{"error": {"code": "INVESTIGATION_NOT_FOUND", "message": "…", "retryable": false, "degraded_mode": false,
           "provider": null, "details": null}}
```
Codes: `VALIDATION_ERROR` 422, `INVESTIGATION_NOT_FOUND`/`SCENARIO_NOT_FOUND`/`LEAD_NOT_FOUND`/`NOT_FOUND` 404,
`CUTOFF_REQUIRED`/`INVALID_ANALYSIS_PARAMETERS`/`UNKNOWN_EVIDENCE_ID`/`UNKNOWN_DOC_ID`/`INVALID_ADDRESS` 422,
`MODE_CONFLICT` 409, `TOO_MANY_INVESTIGATIONS` 429, `UNSUPPORTED_CHAIN`/`UNBOUNDED_REQUEST`/`TOO_MANY_DOCUMENTS` 400,
provider `PROVIDER_*` 502/503/504 (with `degraded_mode: true`), `INTERNAL_ERROR` 500.
Unknown investigation ids return 404 before any parameter validation.

## Person 1 endpoints (unchanged contract; see docs/person1/api-contract.md)
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | status, degraded mode, providers (etherscan, github_issues_search, simulator, fixture), engine version; never secrets |
| GET | `/api/scenarios` | deterministic scenarios + pre-created `investigation_id` |
| POST | `/api/investigations` | `{scenario_id}` or `{chain_id, address, max_transfers≤500, start_time?, end_time?}` |
| GET | `/api/investigations/{id}` | summary incl. `osint_documents`, `osint_sources` counts |
| GET | `/api/investigations/{id}/graph` | nodes/edges (`max_edges≤5000`, asset/time filters) |
| GET | `/api/investigations/{id}/paths` | bounded BFS path tracing (`max_depth≤10`, `max_results≤500`) |
| GET | `/api/investigations/{id}/transactions` | canonical `NormalizedTransfer` records (paginated) |
| GET | `/api/investigations` | list in-memory investigations |

## Intelligence endpoints (Person 2 engine)
| Method | Path | Query/body | Returns |
|---|---|---|---|
| GET | `/api/investigations/{id}/analysis` | `cutoff?, max_hops(1-12), top_k(1-100), rapid_window_seconds, replay_policy` | `case` + full `analysis` (meta, graph_stats, flow, leads, top_k, evidence, claims, case_hypotheses, case_falsification, osint, timeline, boundaries, limitations) |
| GET | `/leads` | `k, offset, max_hops, rapid_window_seconds, cutoff?` | paginated ranked leads + case hypotheses |
| GET | `/evidence` | `lead_id?, cutoff?` | claims, evidence, findings, status semantics |
| GET | `/hypotheses` | `lead_id?, cutoff?` | H1/H2/H3 + falsification (case = top lead) |
| POST | `/sensitivity` | `SensitivityRequest` (below) | re-run engine; `comparison` (per-lead rank/score before/after, moved leads, affected claims, case leading before/after), `leads`, `recomputed: true` |
| GET | `/replay` | `cutoff` (required), `policy=observation|publication`, `top_k` | leads, hypotheses, timeline (`known_at_cutoff`), `excluded` counts |
| GET | `/report` | `format=json|html`, `cutoff?`, `top_k` | casebook (JSON) or escaped HTML with CSP `default-src 'none'` |
| GET | `/flow` | `cutoff?` | FIFO flow decomposition per asset |
| GET | `/route` | `target` (required), `method=hops|fastest|static`, `source?`, `hop_penalty_seconds`, `max_hops` | path node/event ids, hops, cost breakdown |
| GET | `/subgraph` | `center?, max_hops(1-8), max_nodes(2-500), direction` | bounded BFS neighbourhood |
| GET | `/osint` | `cutoff?` | sources, documents, unusable docs, indicators, findings, match summary, independence groups, live adapter health |
| POST | `/osint/documents` | `AnalystDocumentCreate` | stored document + its indicators/findings (201) |
| POST | `/osint/collect` | body: optional list of ≤6 terms; `max_items` | live GitHub collection (409 on synthetic investigations) |
| GET | `/api/simulations/defaults` | – | default `SimulationCreate` |
| POST | `/api/simulations` | `SimulationCreate` | new (or existing, same config) synthetic investigation (201) |
| GET | `/api/evaluation` | `refresh=false` | labelled synthetic benchmark (cached until refresh) |
| * | `/api/intel/*` | – | Person 2's original stateless utilities (`/health`, `/score/*`, `/hypotheses/compare`, `/falsify`, `/features/temporal`, `/osint/extract`, `/osint/match`, `/model/info`) |

### SensitivityRequest
```json
{"remove_evidence_ids": ["EV-TEMP-0x…"], "downgrade_evidence_ids": [], "downgrade_factor": 0.5,
 "remove_doc_ids": ["doc-…"], "config": {"weights": {"osint.indicator_match": 0}}, "cutoff": null}
```
Baseline = same config without weight overrides and without evidence overrides.

### SimulationCreate (all bounded; see `app/schemas/intel.py`)
`seed, scenario_kind (incident|benign|insufficient), depth 1-8, branching 1-6, noise_addresses ≤5000,
noise_transfers ≤50000, disconnected_components, include_cycle, include_self_loop, duplicate_records,
failed_transactions, include_token_leg, repeated_edges, base_amount_eth, start_time, window_hours,
osint_incident_report, osint_service_label, osint_contradiction, osint_stale, osint_missing_timestamp,
osint_ambiguous, osint_duplicate, osint_outage`.

### AnalystDocumentCreate
`source_name, source_type, url (http/https), title?, published_at? (tz-aware), text (≤200k chars),
reliability (rubric 0-1), reliability_rationale, publisher_group?`.

## Core objects
* **Lead**: `lead_id, rank, node_id, address, title, priority_score (0-100), priority_tier, score_type =
  transparent_priority_score_not_probability, components[] (signal, value, weight, points), top_contributors,
  missing_signals, not_applicable_signals, signals, features, reasons, status (candidate|disputed|unresolved),
  support/contradiction/context_evidence_ids, osint{…finding ids, independent_groups}, path, fastest_route,
  hypotheses, falsification, next_check`.
* **Evidence**: `evidence_id, entity, kind, status, title, interpretation, reliability (rubric),
  hypothesis_relations{H1,H2:{direction, strength}}, refs{event_ids|doc_id,indicator_id,finding_id},
  feeds_signals, provenance{source_url, excerpt, match_type, freshness, data_mode, independence_group}, claim_ids`.
* **Claim**: `claim_id, subject, predicate, object, claim_type, status, support/contradiction/context_evidence_ids,
  score_components?, explanation, analysis_version`.
* **Finding** (OSINT↔on-chain): see `docs/OSINT_PIPELINE.md`.

## Example calls
```bash
curl -s localhost:8000/api/investigations/inv-ghost_attacks_synthetic_v1/leads?k=3
curl -s -X POST localhost:8000/api/investigations/inv-ghost_attacks_synthetic_v1/sensitivity \
     -H 'Content-Type: application/json' -d '{"remove_evidence_ids":["EV-TEMP-0x000000000000000000000000000000000000a002"]}'
curl -s "localhost:8000/api/investigations/inv-ghost_attacks_synthetic_v1/replay?cutoff=2026-10-01T02:24:00Z"
curl -s -X POST localhost:8000/api/simulations -H 'Content-Type: application/json' -d '{"seed":7,"scenario_kind":"benign"}'
```
Captured responses: `docs/examples/*.json`, `artifacts/e2e/*.json`.

## Frontend feature → endpoint map
| UI feature (existing component) | Endpoint(s) | Engine/service | Tests |
|---|---|---|---|
| Header/footer data mode, system status, notifications | `/health`, `/analysis` | `healthView`, analysis meta | ui-interactions #1b |
| Overview metrics, activity chart, priority signals, latest transfers | `/analysis`, `/graph` | graph_stats, leads, P1 graph export | ui #1, #2 |
| Graph canvas, node inspector, path transactions, expand | `/analysis`, `/graph` | min-hop paths, flow, evidence | ui #3 |
| Lead queue + detail | `/analysis` | heap Top-K, score_lead, hypotheses, next_check | test_engine, ui |
| Evidence inspector + sensitivity toggle | `/analysis`, `POST /sensitivity` | claims/evidence, compare_runs | test_intel_api, ui #4 |
| OSINT sources | `/analysis`, `POST /osint/collect` (live only) | osint_ingest + correlation | test_intel_api |
| Timeline & replay slider | `/replay?cutoff=` | analyze(cutoff) | test_engine replay, ui #6 |
| Hypotheses + falsification | `/analysis`, `POST /sensitivity` | H1/H2/H3, falsification | ui #5 |
| Evaluation lab | `/api/evaluation` | run_benchmark | test_simulation, ui #7 |
| Casebook export/preview | `/report`, `/report?format=html` | build_report/render_html | test_engine, ui #8 |
| Settings → investigation source | `/api/scenarios`, `POST /api/simulations`, `POST /api/investigations` | simulation, ingestion | ui #9–#11 |
