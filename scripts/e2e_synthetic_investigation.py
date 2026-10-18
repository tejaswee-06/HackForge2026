#!/usr/bin/env python3
"""End-to-end synthetic investigation against a RUNNING ASTRA backend (stdlib only).

    uvicorn app.main:app --app-dir backend --port 8000        # terminal 1
    python scripts/e2e_synthetic_investigation.py http://127.0.0.1:8000 [--out artifacts/e2e]

Steps (prompt section 14.D): health -> create investigation (simulation) -> normalized transfers + graph
-> path trace -> ranked leads -> OSINT correlation + provenance -> contradictory evidence ->
sensitivity recomputation -> historical replay cutoff enforcement -> report export (JSON + HTML).
Every check uses the real routes/payloads; output is written next to the run for inspection.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:8000").rstrip("/")
OUT = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
results = []


def call(method, path, body=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return r.status, (data.decode() if raw else json.loads(data)), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers)


def check(step, name, cond, detail=""):
    results.append({"step": step, "check": name, "ok": bool(cond), "detail": detail})
    print(f"{'PASS' if cond else 'FAIL'} [{step}] {name}" + (f" — {detail}" if detail else ""))


def save(name, obj):
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str))


t0 = time.perf_counter()
s, h, _ = call("GET", "/health")
check(1, "backend healthy, offline demo available", s == 200 and h["status"] == "ok" and h["offline_demo_available"])
check(1, "health exposes no credentials", "apikey" not in json.dumps(h).lower() and "token" not in json.dumps(h).lower().replace("authenticated", ""))

cfg = {"seed": 2026, "scenario_kind": "incident", "depth": 4, "branching": 3}
s, inv, _ = call("POST", "/api/simulations", cfg)
iid = inv["investigation_id"]
check(2, "simulation investigation created (201, synthetic)", s == 201 and inv["data_mode"] == "synthetic", iid)
s2, inv2, _ = call("POST", "/api/simulations", cfg)
check(2, "same seed/config -> same investigation id (deterministic)", inv2["investigation_id"] == iid)
save("investigation.json", inv)

s, tx, _ = call("GET", f"/api/investigations/{iid}/transactions?limit=1000")
check(3, "normalized transactions exist", s == 200 and tx["total"] > 0, f"{tx['total']} transfers")
check(3, "every transfer carries synthetic provenance", all(t["source"]["data_mode"] == "synthetic" for t in tx["transactions"]))
s, g, _ = call("GET", f"/api/investigations/{iid}/graph")
check(3, "graph built from the same records", s == 200 and len(g["edges"]) == tx["total"], f"{len(g['nodes'])} nodes / {len(g['edges'])} edges")
check(3, "duplicates dropped at ingestion", inv["summary"]["duplicates_dropped"] >= 1, str(inv["summary"]["duplicates_dropped"]))

s, p, _ = call("GET", f"/api/investigations/{iid}/paths?max_depth=6&max_results=50")
check(4, "bounded path trace returns time-ordered paths", s == 200 and p["paths"] and all(x["hops"] <= 6 for x in p["paths"]), f"{len(p['paths'])} paths")
s, a, _ = call("GET", f"/api/investigations/{iid}/analysis")
an = a["analysis"]
top = an["leads"][0]
s, r, _ = call("GET", f"/api/investigations/{iid}/route?target={top['address']}&method=fastest")
check(4, "Dijkstra fastest route to the top lead", s == 200 and r["found"], f"{r['hops']} hops, cost {r['cost']}")

s, L, _ = call("GET", f"/api/investigations/{iid}/leads?k=10")
check(5, "ranked leads with score components and reasons", s == 200 and L["leads"] and all(x["components"] and x["reasons"] for x in L["leads"]))
check(5, "scores labelled as non-probabilistic", L["score_type"] == "transparent_priority_score_not_probability")
check(5, "deterministic ranking", call("GET", f"/api/investigations/{iid}/leads?k=10")[1]["snapshot_id"] == L["snapshot_id"])
save("leads.json", L)

s, o, _ = call("GET", f"/api/investigations/{iid}/osint")
check(6, "OSINT documents collected with provenance", s == 200 and o["documents"] and all(d["url"] and d["retrieved_at"] and d["content_hash"] for d in o["documents"]))
check(6, "indicator matches graded", set(o["match_summary"]) >= {"exact", "ambiguous"}, json.dumps(o["match_summary"]))
check(6, "simulated source outage reported, not hidden", bool(o["documents_unusable"]))
check(7, "contradictory evidence represented", any(f["contradictory"] for f in o["findings"]))
s, ev, _ = call("GET", f"/api/investigations/{iid}/evidence")
disputed = [c for c in ev["claims"] if c["status"] == "Disputed"]
check(7, "disputed claims link support AND contradiction evidence", disputed and all(c["support_evidence_ids"] and c["contradiction_evidence_ids"] for c in disputed if c["claim_type"] == "source_reported_association"), f"{len(disputed)} disputed claims")
save("evidence.json", ev)

clue = next(e for e in top["support_evidence_ids"])
s, sens, _ = call("POST", f"/api/investigations/{iid}/sensitivity", {"remove_evidence_ids": [clue]})
row = next(x for x in sens["comparison"]["leads"] if x["lead_id"] == top["lead_id"])
check(8, "sensitivity re-runs the engine and changes the result", s == 200 and sens["recomputed"] and row["score_after"] != row["score_before"],
      f"{clue}: {row['score_before']} -> {row['score_after']} (rank {row['rank_before']} -> {row['rank_after']})")
save("sensitivity.json", sens)

times = sorted(t["time"] for t in an["timeline"] if t["kind"] == "transfer")
cut = times[len(times) // 3]
s, rp, _ = call("GET", f"/api/investigations/{iid}/replay?cutoff={cut}")
leak = [t for t in rp["timeline"] if t["known_at_cutoff"] and t["time"] > cut]
ev_leak = [eid for x in rp["leads"] for eid in (x["path"] or {}).get("event_ids", [])
           if next(t for t in an["timeline"] if t["ref"] == eid)["time"] > cut]
check(9, "replay excludes records after the cutoff", s == 200 and not leak and not ev_leak and rp["excluded"]["transfers_after_cutoff"] > 0,
      f"cutoff {cut}: {rp['excluded']['transfers_after_cutoff']} transfers, {len(rp['excluded']['documents_after_cutoff'])} docs excluded")
check(9, "replay is reproducible", call("GET", f"/api/investigations/{iid}/replay?cutoff={cut}")[1]["snapshot_id"] == rp["snapshot_id"])
save("replay.json", rp)

s, rep, hd = call("GET", f"/api/investigations/{iid}/report")
R = rep["report"]
check(10, "JSON report matches on-screen leads", [x["lead_id"] for x in R["leads"]] == [x["lead_id"] for x in L["leads"]])
check(10, "report records version, config, snapshot and limitations", R["analysis"]["engine_version"] and R["analysis"]["config_hash"] and R["limitations"])
check(10, "report labelled SYNTHETIC", R["data_label"] == "SYNTHETIC / SIMULATED")
s, html, hd = call("GET", f"/api/investigations/{iid}/report?format=html", raw=True)
check(10, "HTML report served with restrictive CSP", s == 200 and "default-src 'none'" in hd.get("content-security-policy", "") and "SYNTHETIC / SIMULATED" in html)
save("report.json", rep)
save("report.html", html)

ok = all(x["ok"] for x in results)
summary = {"base_url": BASE, "investigation_id": iid, "simulation_config": cfg, "checks": len(results),
           "passed": sum(x["ok"] for x in results), "elapsed_s": round(time.perf_counter() - t0, 3), "results": results}
save("e2e-summary.json", summary)
print(f"\n{summary['passed']}/{summary['checks']} checks passed in {summary['elapsed_s']}s — {'ALL PASSED' if ok else 'FAILURES'}")
sys.exit(0 if ok else 1)
