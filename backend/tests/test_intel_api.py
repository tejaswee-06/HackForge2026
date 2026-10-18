"""Integration tests across Person 1 API <-> Person 2 engine <-> OSINT adapters (HTTP mocked)."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import INV, SECRET, WALLET, etherscan_handler, settings

META = {"investigation_id", "data_mode", "analysis_version", "generated_at", "data_cutoff", "limitations", "sources",
        "data_label", "snapshot_id", "config_hash"}
GOOD = {"hash": "0x" + "a" * 64, "blockNumber": "1", "timeStamp": "1790000000", "from": WALLET,
        "to": "0x" + "0" * 36 + "beef", "value": "5000", "isError": "0"}
GH_TOKEN = "GH-TOKEN-NEVER-LEAK-999"


def err(r):
    return r.json()["error"]


def gh_handler(calls=None, mode="ok"):
    def h(req: httpx.Request):
        if calls is not None:
            calls.append(req)
        if mode == "ratelimit":
            return httpx.Response(403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "99999999999"},
                                  json={"message": "API rate limit exceeded"})
        if mode == "timeout":
            raise httpx.ReadTimeout("slow")
        if mode == "malformed":
            return httpx.Response(200, json={"items": "nope"})
        q = req.url.params["q"]
        return httpx.Response(200, json={"items": [{
            "html_url": "https://github.com/example/repo/issues/1", "title": "Exploit report",
            "body": f"The attacker wallet {q.strip(chr(34))} drained funds.", "created_at": "2023-11-14T22:13:21Z",
            "repository_url": "https://api.github.com/repos/example/repo"}]})
    return h


def live(gh_mode="ok", calls=None, gh_enabled=True):
    app = create_app(settings(etherscan_api_key=SECRET, osint_github_enabled=gh_enabled, github_token=GH_TOKEN),
                     etherscan_transport=httpx.MockTransport(etherscan_handler(native=[GOOD])),
                     github_transport=httpx.MockTransport(gh_handler(calls, gh_mode)), sleep=lambda s: None)
    c = TestClient(app)
    inv = c.post("/api/investigations", json={"chain_id": 1, "address": WALLET}).json()["investigation_id"]
    return c, inv


def test_analysis_bundle_has_shared_metadata_and_labels(client):
    j = client.get(f"/api/investigations/{INV}/analysis").json()
    assert META <= set(j) and j["data_label"] == "SYNTHETIC / SIMULATED" and j["synthetic"] is True
    a = j["analysis"]
    assert a["leads"] and a["evidence"] and a["claims"] and a["osint"]["findings"] and a["timeline"]
    assert j["case"]["investigation_id"] == INV


def test_leads_pagination_and_determinism(client):
    a = client.get(f"/api/investigations/{INV}/leads?k=3").json()
    b = client.get(f"/api/investigations/{INV}/leads?k=3&offset=3").json()
    assert [L["rank"] for L in a["leads"]] == [1, 2, 3] and b["leads"][0]["rank"] == 4
    assert a["snapshot_id"] == client.get(f"/api/investigations/{INV}/leads?k=3").json()["snapshot_id"]
    assert client.get(f"/api/investigations/{INV}/leads?k=0").status_code == 422


def test_sensitivity_recomputes_via_engine(client):
    app = client.app
    base = client.get(f"/api/investigations/{INV}/leads").json()
    top = base["leads"][0]
    ev = next(e for e in top["support_evidence_ids"] if e.startswith("EV-TEMP-"))
    before = app.state.analysis.runs
    r = client.post(f"/api/investigations/{INV}/sensitivity", json={"remove_evidence_ids": [ev]})
    assert r.status_code == 200 and r.json()["recomputed"] is True
    assert app.state.analysis.runs == before + 1  # baseline cached, counterfactual computed
    row = next(x for x in r.json()["comparison"]["leads"] if x["lead_id"] == top["lead_id"])
    assert row["score_after"] < row["score_before"] and r.json()["comparison"]["affected_claim_ids"]
    w = client.post(f"/api/investigations/{INV}/sensitivity", json={"config": {"weights": {"osint.indicator_match": 0}}})
    assert w.status_code == 200 and w.json()["comparison"]["moved_leads"]


def test_sensitivity_validation(client):
    assert err(client.post(f"/api/investigations/{INV}/sensitivity", json={"remove_evidence_ids": ["X"]}))["code"] == "UNKNOWN_EVIDENCE_ID"
    assert err(client.post(f"/api/investigations/{INV}/sensitivity", json={"remove_doc_ids": ["X"]}))["code"] == "UNKNOWN_DOC_ID"
    r = client.post(f"/api/investigations/{INV}/sensitivity", json={"downgrade_factor": 3})
    assert r.status_code == 422 and err(r)["code"] == "VALIDATION_ERROR"
    r = client.post(f"/api/investigations/{INV}/sensitivity", json={"config": {"weights": {"bogus": 1}}})
    assert r.status_code == 422 and err(r)["code"] == "INVALID_ANALYSIS_PARAMETERS"


def test_replay_api_cutoff_enforced(client):
    r = client.get(f"/api/investigations/{INV}/replay?cutoff=2026-10-01T02:20:00Z").json()
    assert r["cutoff"] == "2026-10-01T02:20:00Z" and r["excluded"]["transfers_after_cutoff"] >= 1
    assert r["osint_documents"] == [] and r["data_cutoff"] == "2026-10-01T02:20:00Z"
    for t in r["timeline"]:
        if t["known_at_cutoff"]:
            assert t["time"] <= "2026-10-01T02:20:00Z"
    p = client.get(f"/api/investigations/{INV}/replay?cutoff=2026-10-01T03:00:00Z&policy=publication").json()
    assert p["osint_documents"] and p["policy"] == "publication"
    assert client.get(f"/api/investigations/{INV}/replay?cutoff=yesterday").status_code == 422


def test_report_json_and_html(client):
    j = client.get(f"/api/investigations/{INV}/report").json()
    rep = j["report"]
    assert rep["data_label"] == "SYNTHETIC / SIMULATED" and rep["leads"] and rep["source_register"]
    leads = client.get(f"/api/investigations/{INV}/leads").json()["leads"]
    assert [L["lead_id"] for L in rep["leads"]] == [L["lead_id"] for L in leads]
    h = client.get(f"/api/investigations/{INV}/report?format=html")
    assert h.status_code == 200 and "SYNTHETIC / SIMULATED" in h.text and "default-src 'none'" in h.headers["content-security-policy"]


def test_route_subgraph_flow_osint_endpoints(client):
    tgt = "0x000000000000000000000000000000000000a006"
    for m in ("hops", "fastest", "static"):
        r = client.get(f"/api/investigations/{INV}/route?target={tgt}&method={m}").json()
        assert r["found"] and r["event_ids"]
    assert client.get(f"/api/investigations/{INV}/route?target=0x12").status_code == 422
    s = client.get(f"/api/investigations/{INV}/subgraph?max_hops=1&max_nodes=3").json()
    assert len(s["nodes"]) <= 3 and s["found"]
    f = client.get(f"/api/investigations/{INV}/flow").json()
    assert f["flow"][0]["conservation_ok"]
    o = client.get(f"/api/investigations/{INV}/osint").json()
    assert o["match_summary"]["ambiguous"] == 1 and o["live_adapters"]["github_issues_search"]["enabled"] is False


def test_analyst_document_triggers_new_matches(client):
    before = client.get(f"/api/investigations/{INV}/osint").json()["snapshot_id"]
    body = {"source_name": "Analyst", "url": "https://example.org/n", "text": "Exploit funds reached 0x000000000000000000000000000000000000a004 (attacker)."}
    r = client.post(f"/api/investigations/{INV}/osint/documents", json=body)
    assert r.status_code == 201 and r.json()["findings"][0]["match_type"] == "exact"
    assert r.json()["document"]["data_mode"] == "synthetic"  # follows the investigation's mode
    assert client.get(f"/api/investigations/{INV}/osint").json()["snapshot_id"] != before
    bad = client.post(f"/api/investigations/{INV}/osint/documents", json={**body, "url": "javascript:alert(1)"})
    assert bad.status_code == 422


def test_live_collection_refused_for_synthetic(client):
    r = client.post(f"/api/investigations/{INV}/osint/collect")
    assert r.status_code == 409 and err(r)["code"] == "MODE_CONFLICT"


def test_live_collection_from_github_with_provenance_and_no_token_leak():
    calls = []
    c, inv = live(calls=calls)
    r = c.post(f"/api/investigations/{inv}/osint/collect", json=[WALLET])
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "ok"
    assert calls and calls[0].headers["authorization"] == f"Bearer {GH_TOKEN}"
    o = c.get(f"/api/investigations/{inv}/osint").json()
    d = o["documents"][0]
    assert d["data_mode"] == "real" and d["url"].startswith("https://github.com/") and d["extraction_method"] == "github_issues_api_v1"
    assert o["findings"][0]["match_type"] == "exact" and o["data_label"] == "LIVE"
    assert GH_TOKEN not in str(o) and GH_TOKEN not in c.get("/health").text and SECRET not in c.get("/health").text


@pytest.mark.parametrize("mode,code", [("ratelimit", "OSINT_RATE_LIMITED"), ("timeout", "OSINT_TIMEOUT"),
                                       ("malformed", "OSINT_BAD_RESPONSE")])
def test_live_collection_failures_are_reported_not_hidden(mode, code):
    calls = []
    c, inv = live(gh_mode=mode, calls=calls)
    r = c.post(f"/api/investigations/{inv}/osint/collect", json=[WALLET]).json()
    assert r["results"][0]["status"] == "error" and r["results"][0]["code"] == code
    o = c.get(f"/api/investigations/{inv}/osint").json()
    assert o["documents"] == [] and any(s["status"] in ("failed", "rate_limited") for s in o["sources"])
    if mode == "timeout":
        assert len(calls) == 3  # 1 + 2 bounded retries


def test_live_collection_disabled_by_default():
    c, inv = live(gh_enabled=False)
    r = c.post(f"/api/investigations/{inv}/osint/collect", json=[WALLET]).json()
    assert r["results"][0]["code"] == "OSINT_SOURCE_DISABLED"


def test_live_investigation_analysis_is_live_labelled():
    c, inv = live()
    a = c.get(f"/api/investigations/{inv}/analysis").json()
    assert a["data_mode"] == "real" and a["data_label"] == "LIVE" and a["synthetic"] is False
    assert a["analysis"]["leads"][0]["address"] == "0x" + "0" * 36 + "beef"


def test_simulation_endpoint_is_deterministic_and_labelled(client):
    r1 = client.post("/api/simulations", json={"seed": 21, "scenario_kind": "benign"})
    r2 = client.post("/api/simulations", json={"seed": 21, "scenario_kind": "benign"})
    assert r1.status_code == 201 and r1.json()["investigation_id"] == r2.json()["investigation_id"]
    inv = r1.json()["investigation_id"]
    a = client.get(f"/api/investigations/{inv}/analysis").json()
    assert a["data_label"] == "SYNTHETIC / SIMULATED" and a["analysis"]["case_hypotheses"]["leading"] == "H2"
    assert "ground_truth" not in str(a)
    assert client.post("/api/simulations", json={"seed": -1}).status_code == 422
    assert client.post("/api/simulations", json={"depth": 99}).status_code == 422
    assert client.get("/api/simulations/defaults").json()["label"] == "SYNTHETIC / SIMULATED"


def test_investigation_limit():
    c = TestClient(create_app(settings(max_investigations=2)))
    assert c.post("/api/simulations", json={"seed": 1}).status_code == 201
    r = c.post("/api/simulations", json={"seed": 2})
    assert r.status_code == 429 and err(r)["code"] == "TOO_MANY_INVESTIGATIONS"


def test_evaluation_endpoint_measures(client):
    client.app.state.settings.__dict__  # frozen dataclass; use a small seed list via a fresh app instead
    c = TestClient(create_app(settings(evaluation_seeds=(1,))))
    j = c.get("/api/evaluation").json()
    assert j["aggregate"]["incident_scenarios"] == 1 and j["benchmark_type"].startswith("controlled_synthetic")
    assert c.get("/api/evaluation").json()["generated_at"] == j["generated_at"]  # cached until refresh


def test_cors_allows_dev_frontend_only(client):
    ok = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_stateless_person2_utilities_are_mounted(client):
    assert client.get("/api/intel/health").json()["service"] == "astra-person2-intelligence"
    r = client.post("/api/intel/osint/extract", json={"text": "x 0x" + "a" * 40, "source_url": "https://e.org", "source_name": "e"})
    assert r.status_code == 200 and r.json()["candidate_indicators"]
