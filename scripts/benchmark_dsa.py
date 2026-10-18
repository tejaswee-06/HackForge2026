#!/usr/bin/env python3
"""Reproducible performance measurements for the DSA layer and the full engine.

    python scripts/benchmark_dsa.py [--out artifacts/benchmarks/dsa.json] [--sizes 1000,10000,100000]

Graphs: seeded random temporal multigraphs (n = edges/4 nodes, timestamps uniform over one day, one
asset) plus the full engine on simulated investigations with growing noise. Wall-clock seconds from
time.perf_counter() (median of 3 runs); hardware/OS details are recorded with the results.
"""
import json
import os
import platform
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from astra_intel import dsa  # noqa: E402
from astra_intel.dsa import Edge, GraphIndex  # noqa: E402


def graph(m: int, seed: int = 7):
    rnd = random.Random(seed)
    n = max(10, m // 4)
    edges = [Edge(f"e{i}", f"1:n{rnd.randrange(n)}", f"1:n{rnd.randrange(n)}", rnd.randrange(86400), "1:native",
                  rnd.randrange(1, 10 ** 6), f"0x{i:064x}") for i in range(m)]
    return edges, n


def timeit(fn, reps=3):
    ts = []
    out = None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn()
        ts.append(time.perf_counter() - t)
    return round(statistics.median(ts), 5), out


def main():
    sizes = [int(x) for x in (sys.argv[sys.argv.index("--sizes") + 1] if "--sizes" in sys.argv else "1000,10000,100000").split(",")]
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "artifacts" / "benchmarks" / "dsa.json"
    rows = []
    for m in sizes:
        edges, n = graph(m)
        tb, g = timeit(lambda: GraphIndex(edges))
        s = "1:n0"
        r = {"edges": m, "nodes": len(g.nodes), "build_index_s": tb}
        r["bfs_neighbourhood_s"], d = timeit(lambda: dsa.bfs_neighborhood(g, s, max_hops=3, max_nodes=10 ** 9))
        r["bfs_neighbourhood_nodes"] = len(d)
        r["dfs_time_respecting_reach_s"], rr = timeit(lambda: dsa.time_respecting_reachability(g, s, start_time=0, max_expansions=10 ** 9))
        r["reachable_nodes"] = len(rr.earliest_arrival)
        r["min_hop_layered_bfs_s"], mh = timeit(lambda: dsa.min_hop_time_respecting(g, s, start_time=0, max_hops=6))
        tgt = max(mh, key=lambda k: (mh[k].hops, k)) if mh else "1:n1"
        r["dijkstra_fastest_s"], dj = timeit(lambda: dsa.dijkstra_fastest_route(g, s, tgt, start_time=0, max_expansions=10 ** 7))
        r["dijkstra_found"] = dj.found
        r["flow_decomposition_s"], fl = timeit(lambda: dsa.decompose_flow(g, s, "1:native", start_time=0))
        r["flow_conservation_ok"] = fl.conservation_ok
        r["union_find_components_s"], uf = timeit(lambda: dsa.weak_components(g))
        r["cycle_detection_s"], _ = timeit(lambda: dsa.has_directed_cycle(g))
        items = [(f"x{i}", random.Random(i).random()) for i in range(m)]
        r["top_k_heap_k10_s"], _ = timeit(lambda: dsa.top_k(items, 10, score=lambda x: x[1], tiebreak=lambda x: x[0]))
        r["full_sort_reference_s"], _ = timeit(lambda: sorted(items, key=lambda x: (-x[1], x[0]))[:10])
        rows.append(r)
        print(json.dumps(r))

    # full engine on simulated investigations of increasing size (same code path as the API)
    from app.schemas.intel import SimulationCreate
    from app.services.evaluation import run_scenario
    eng = []
    for noise in (20, 2000, 20000):
        cfg = SimulationCreate(seed=1, noise_addresses=max(12, noise // 10), noise_transfers=noise).model_dump()
        t = time.perf_counter()
        g_, res, nb = run_scenario(cfg)
        eng.append({"noise_transfers": noise, "events": len(nb.events), "leads": len(res["leads"]),
                    "documents": len(res["osint"]["documents"]), "end_to_end_s": round(time.perf_counter() - t, 4)})
        print(json.dumps(eng[-1]))
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(), "method": __doc__.strip(),
           "environment": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system(),
                           "processor": platform.processor() or "unknown", "cpu_count": os.cpu_count()},
           "dsa": rows, "engine": eng}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
