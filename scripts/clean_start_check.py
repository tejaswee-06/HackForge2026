#!/usr/bin/env python3
"""End-to-end check against a RUNNING server (stdlib only).

    uvicorn app.main:app --app-dir backend --port 8000   # in one terminal
    python scripts/clean_start_check.py http://127.0.0.1:8000 [--save-examples]
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:8000").rstrip("/")
SAVE = "--save-examples" in sys.argv
ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "docs" / "examples"
failures = []


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def save(name, obj):
    if SAVE:
        EX.mkdir(parents=True, exist_ok=True)
        (EX / name).write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")


scen = json.loads((ROOT / "data/scenarios/ghost_attacks_synthetic_v1.json").read_text())
exp = scen["expected"]
s, h = call("GET", "/health"); save("health.json", h)
check("1 health 200 + offline demo available", s == 200 and h["offline_demo_available"] and h["default_data_mode"] == "synthetic")
s, sc = call("GET", "/api/scenarios"); save("scenarios.json", sc)
check("2 scenarios lists synthetic scenario", s == 200 and sc["scenarios"][0]["data_mode"] == "synthetic")
sid = sc["scenarios"][0]["scenario_id"]
s, inv = call("POST", "/api/investigations", {"scenario_id": sid}); save("create_investigation_request.json", {"scenario_id": sid}); save("investigation.json", inv)
check("3 create scenario investigation (201, synthetic)", s == 201 and inv["data_mode"] == "synthetic")
iid = inv["investigation_id"]
s, g = call("GET", f"/api/investigations/{iid}/graph"); save("graph.json", g)
check("4 graph counts match oracle", s == 200 and len(g["nodes"]) == exp["counts"]["graph_nodes"] and len(g["edges"]) == exp["counts"]["graph_edges"])
s, p = call("GET", f"/api/investigations/{iid}/paths?max_depth=6&asset_id=1:native"); save("paths.json", p)
want = {tuple(x["event_ids"]) for x in exp["paths_native_from_start_depth6"]}
check("5 expected paths + amounts reproduced", s == 200 and {tuple(x["event_ids"]) for x in p["paths"]} == want and
      {x["bottleneck_raw"] for x in p["paths"]} == {x["bottleneck_raw"] for x in exp["paths_native_from_start_depth6"]})
s, t = call("GET", f"/api/investigations/{iid}/transactions?limit=3"); save("transactions.json", t)
check("6 canonical transactions endpoint", s == 200 and t["total"] == 11 and len(t["transactions"]) == 3)
s, e = call("GET", "/api/investigations/does-not-exist"); save("error_404.json", e)
check("7 unknown id -> 404 envelope", s == 404 and e["error"]["code"] == "INVESTIGATION_NOT_FOUND")
s, e = call("POST", "/api/investigations", {"chain_id": 1, "address": "0x123"}); save("error_422.json", e)
check("8 invalid input -> 422 envelope", s == 422 and e["error"]["code"] == "VALIDATION_ERROR")
s, e = call("POST", "/api/investigations", {"chain_id": 1, "address": "0x" + "0" * 36 + "c0de"}); save("error_503_provider.json", e)
live_ok = s == 201
check("9 live wallet: explicit degraded error when no key (never silent synthetic)",
      (s == 503 and e["error"]["degraded_mode"] is True) or live_ok)
s, l = call("GET", f"/api/investigations/{iid}/leads"); save("leads.json", l)
check("10 Person-2 leads endpoint served by the engine (synthetic-labelled)", s == 200 and l["data_label"] == "SYNTHETIC / SIMULATED" and len(l["leads"]) > 0)
check("11 health exposes no credential material", "apikey" not in json.dumps(h).lower() and "secret" not in json.dumps(h).lower())
print(f"\n{'ALL CHECKS PASSED' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
