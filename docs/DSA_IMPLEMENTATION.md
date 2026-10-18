# DSA implementation (Person 2) — `backend/astra_intel/dsa.py`

All algorithms run behind the existing investigation workflow; there is no "DSA page". Every function
is iterative (no recursion limits), bounded, and covered by `tests/intel/test_dsa.py` (18 tests on
small hand-checked fixtures with cycles, self-loops, repeated edges, disconnected components, empty
graphs, time-window boundaries and ties) plus engine/API tests.

| # | Algorithm | Powers (feature) | Input → output | Complexity | Assumptions / limitations |
|---|---|---|---|---|---|
| 1 | **GraphIndex**: adjacency lists (`out_adj`, `in_adj`, sorted by time) + hash maps (`by_event`, `by_tx_hash`, `by_address`) | every engine step; tx-hash OSINT matching (`by_tx_hash`); shortened-address matching (`nodes`) | canonical transfers → index | build O(E log E), lookup O(1) avg, memory O(V+E) | multigraph: repeated transfers stay separate edges; duplicate event ids ignored and counted |
| 2a | **BFS neighbourhood** `bfs_neighborhood` | `/subgraph` (bounded graph view) | start, max_hops, max_nodes, direction → node→hops | O(V'+E') | ignores time/asset (it is a view, not a trace) |
| 2b | **Iterative DFS, label-correcting** `time_respecting_reachability` | candidate-lead set; "missing link" signal | start, start_time, window → earliest arrival per node | O(E·improvements), capped by `max_expansions` | funds cannot leave before they arrive (non-decreasing timestamps); cycles terminate because labels only improve |
| 2c | **Three-colour DFS** `has_directed_cycle` | graph statistics in overview/report | graph → bool | O(V+E) | time ignored; self-loop counts as a cycle |
| 3a | **Layered BFS (time-respecting min hops)** `min_hop_time_respecting` | `incident_path_proximity = 1/hops`; "observed path" evidence; `/route?method=hops` | src, start_time, max_hops → node→path | O(max_hops·E) | exact: for each k keeps earliest arrival; a node may re-appear later with an earlier arrival (tested) |
| 3b | **Static BFS** `bfs_min_hops_static` | evaluation baseline; "path only if time ignored" (missing-link 0.5) | src,dst → path | O(V+E) | can produce time-impossible routes — labelled as such |
| 3c | **Dijkstra over edge states** `dijkstra_fastest_route` | `fastest_route` on top-k leads; `/route?method=fastest` | src,dst,hop_penalty → route + cost breakdown | O(S log S), capped | cost = Σ(hop_penalty_seconds + dwell seconds), dwell = time value waited at the intermediate address (≥0 because routes are time-respecting). **Amounts are not used as distances.** State = last edge because cost depends on arrival time |
| 4 | **Heap Top-K** `top_k` (size-k min-heap) | lead queue ranking and `top_k`; precision@k in evaluation | items, k, score, tiebreak → k best | O(n log k), O(k) memory | ties: score desc, then (hops asc, node id asc) → deterministic (tested vs sorted reference and shuffled input) |
| 5 | **FIFO flow decomposition** `decompose_flow` | `flow_continuity` signal, "traced value" evidence, residual (unresolved) boundaries, `/flow`, graph node values | start, asset → path flows with event ids, traced/untraced per edge, residual per node | O(E log E + E·P), P ≤ 64 parcels/node | initial = origin's observed outflow; FIFO spending of traced parcels; remainder untraced; time-respecting; one asset; integer arithmetic; conservation Σflows = initial is checked (`conservation_ok`). An allocation, **not proof the same funds moved**; LIFO/pro-rata give different answers |
| 6 | **Union-Find** (path compression + union by size) | weak components (graph stats, `in_incident_component`); **OSINT independence groups** (same content hash or publisher ⇒ not independent corroboration) | symmetric pairs → groups | ~O(α(n)) per op | used only for symmetric relations; never for directed or time-aware tracing |
| 7 | Supporting metrics: `percentile_ranks` (mid-rank ties), `median`, receipt→forward deltas via `bisect` on sorted in-edge times | `unusual_degree`, `rapid_forwarding`, temporal evidence | – | O(n log n) / O(out·log in) | timing similarity is a signal, not proof of coordination |

## Measured performance (`scripts/benchmark_dsa.py`, median of 3 runs)
Environment: Linux x86_64, Python 3.13.16, 2 vCPU container. Random temporal multigraphs (nodes = edges/4,
timestamps uniform over one day). Raw results: `artifacts/benchmarks/dsa.json`.

| Edges | Build index | BFS 3 hops | DFS reach | Layered BFS | Dijkstra | Flow decomposition | Union-Find | Cycle DFS | Heap top-10 | sort top-10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1,000 | 1.05 ms | 0.23 ms | 0.04 ms | 0.10 ms | 0.06 ms | 0.59 ms | 0.62 ms | 0.05 ms | 0.34 ms | 0.24 ms |
| 10,000 | 14.1 ms | 0.39 ms | 0.02 ms | 0.04 ms | 0.03 ms | 7.5 ms | 6.8 ms | 0.75 ms | 3.3 ms | 3.5 ms |
| 100,000 | 295 ms | 0.43 ms | 0.06 ms | 0.17 ms | 0.13 ms | 151 ms | 122 ms | 12.5 ms | 33 ms | 50 ms |

Notes: the time-respecting searches touch few nodes in these random graphs because of the time-order
constraint (56/25/100 reachable nodes), so their timings reflect that. Heap top-k is faster than a
full sort only at larger n in pure Python; the advantage is O(k) memory and O(n log k) growth.

Full engine (simulation → P1 normalizer → engine, one process): 125 events 0.055 s; 2,199 events 0.17 s;
21,099 events 1.29 s.

## Edge cases covered by tests
Empty graph; isolated/disconnected components; directed cycles (incl. through flow decomposition);
self-loops (skipped in flow, counted in stats); repeated edges; duplicate event ids; time-window
boundaries (inclusive); expansion budgets; 5,000-hop chains (no recursion); deterministic ties;
unlike assets never mixed.
