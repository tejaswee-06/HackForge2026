import httpx
import pytest

from .conftest import INV, SCEN_ID, SECRET, WALLET, etherscan_handler, live_client

META = {"investigation_id", "data_mode", "analysis_version", "generated_at", "data_cutoff", "limitations", "sources"}
GOOD = {"hash": "0x" + "a" * 64, "blockNumber": "1", "timeStamp": "1790000000", "from": WALLET,
        "to": "0x" + "0" * 36 + "beef", "value": "5", "isError": "0"}


def err(r):
    return r.json()["error"]


def test_health_offline(client):
    j = client.get("/health").json()
    assert j["status"] == "ok" and j["offline_demo_available"] and j["default_data_mode"] == "synthetic"
    assert j["degraded_mode"] is True and "not configured" in j["degraded_reason"]
    assert j["providers"]["etherscan"]["status"] == "not_configured" and j["providers"]["fixture"]["data_mode"] == "synthetic"


def test_health_never_exposes_secret():
    c = live_client(etherscan_handler())
    j = c.get("/health").json()
    assert SECRET not in str(j) and j["providers"]["etherscan"]["configured"] is True and j["degraded_mode"] is False


def test_scenarios_listing(client, expected):
    j = client.get("/api/scenarios").json()
    s = j["scenarios"][0]
    assert s["scenario_id"] == SCEN_ID and s["data_mode"] == "synthetic" and s["synthetic"] is True
    assert s["investigation_id"] == INV and s["event_count"] == 13


def test_create_scenario_investigation_is_synthetic_and_deterministic(client, expected):
    r = client.post("/api/investigations", json={"scenario_id": SCEN_ID})
    assert r.status_code == 201
    j = r.json()
    assert META <= set(j) and j["data_mode"] == "synthetic" and j["investigation_id"] == INV
    assert j["sources"][0]["provider"] == "astra-fixture" and any("SYNTHETIC" in l for l in j["limitations"])
    c = expected["counts"]
    assert (j["summary"]["node_count"], j["summary"]["edge_count"], j["summary"]["duplicates_dropped"]) == (
        c["graph_nodes"], c["graph_edges"], c["duplicates_dropped"])
    again = client.post("/api/investigations", json={"scenario_id": SCEN_ID}).json()
    assert again["investigation_id"] == j["investigation_id"] and again["summary"] == j["summary"]


def test_get_investigation(client):
    j = client.get(f"/api/investigations/{INV}").json()
    assert j["status"] == "ready" and j["chain_id"] == 1 and j["scope"]["type"] == "scenario"
    assert j["data_cutoff"].startswith("2026-10-01T02:34")  # last event t+20min


def test_graph_retrieval_and_contract(client, expected):
    j = client.get(f"/api/investigations/{INV}/graph").json()
    assert META <= set(j) and j["data_mode"] == "synthetic"
    assert len(j["nodes"]) == 10 and len(j["edges"]) == 11 and j["stats"]["truncated"] is False
    e = j["edges"][0]
    assert {"edge_id", "source", "target", "transaction_hash", "timestamp", "asset_id", "raw_value", "provenance"} <= set(e)
    assert e["provenance"]["data_mode"] == "synthetic"
    ids = {n["node_id"] for n in j["nodes"]}
    assert all(x["source"] in ids and x["target"] in ids for x in j["edges"])


def test_graph_asset_filter_and_empty_graph(client):
    j = client.get(f"/api/investigations/{INV}/graph", params={"asset_id": "1:native"}).json()
    assert len(j["edges"]) == 9
    j = client.get(f"/api/investigations/{INV}/graph", params={"asset_id": "1:erc20:0x" + "1" * 40}).json()
    assert j["nodes"] == [] and j["edges"] == [] and any("empty" in n for n in j["notes"])


def test_known_expected_path_via_api(client, expected):
    j = client.get(f"/api/investigations/{INV}/paths", params={"max_depth": 6, "asset_id": "1:native"}).json()
    got = {tuple(p["event_ids"]) for p in j["paths"]}
    want = {tuple(p["event_ids"]) for p in expected["paths_native_from_start_depth6"]}
    assert got == want and j["start_found"] is True and j["data_mode"] == "synthetic"
    assert any("do not prove common ownership" in n for n in j["notes"])


def test_paths_unknown_start_and_invalid_address(client):
    j = client.get(f"/api/investigations/{INV}/paths", params={"start": "0x" + "9" * 40}).json()
    assert j["start_found"] is False and j["paths"] == []
    r = client.get(f"/api/investigations/{INV}/paths", params={"start": "nope"})
    assert r.status_code == 422 and err(r)["code"] == "INVALID_ADDRESS"


def test_paths_bounds_validated(client):
    for params in ({"max_depth": 0}, {"max_depth": 11}, {"max_results": 501}):
        r = client.get(f"/api/investigations/{INV}/paths", params=params)
        assert r.status_code == 422 and err(r)["code"] == "VALIDATION_ERROR"


def test_transactions_are_canonical(client):
    j = client.get(f"/api/investigations/{INV}/transactions").json()
    assert j["total"] == 11  # successful only by default
    t = j["transactions"][0]
    assert {"event_id", "chain_id", "transaction_hash", "timestamp", "from_address", "to_address", "asset_id",
            "raw_value", "status", "source"} <= set(t) and isinstance(t["raw_value"], str)
    assert client.get(f"/api/investigations/{INV}/transactions", params={"include_non_success": True}).json()["total"] == 12


def test_unknown_investigation_404_envelope(client):
    for path in ("", "/graph", "/paths", "/transactions", "/leads", "/evidence", "/replay", "/report"):
        r = client.get(f"/api/investigations/nope{path}")
        assert r.status_code == 404 and err(r)["code"] == "INVESTIGATION_NOT_FOUND"
    assert client.post("/api/investigations/nope/sensitivity").status_code == 404
    assert client.get("/no/such/route").status_code == 404 and err(client.get("/no/such/route"))["code"] == "NOT_FOUND"


def test_unknown_scenario_404(client):
    r = client.post("/api/investigations", json={"scenario_id": "nope"})
    assert r.status_code == 404 and err(r)["code"] == "SCENARIO_NOT_FOUND" and SCEN_ID in err(r)["details"]["available"]


@pytest.mark.parametrize("body", [
    {}, {"scenario_id": SCEN_ID, "address": WALLET}, {"chain_id": 1}, {"address": WALLET},
    {"chain_id": 1, "address": "0x123"}, {"chain_id": 1, "address": WALLET, "max_transfers": 501},
    {"chain_id": 1, "address": WALLET, "max_transfers": 0},
    {"chain_id": 1, "address": WALLET, "start_time": "2026-10-02T00:00:00Z", "end_time": "2026-10-01T00:00:00Z"},
    {"chain_id": 1, "address": WALLET, "start_time": "2026-10-01T00:00:00"},
    {"scenario_id": SCEN_ID, "unexpected": 1},
])
def test_invalid_create_requests_rejected(client, body):
    r = client.post("/api/investigations", json=body)
    assert r.status_code == 422 and err(r)["code"] == "VALIDATION_ERROR" and err(r)["details"]


def test_unsupported_chain_rejected(client):
    r = client.post("/api/investigations", json={"chain_id": 137, "address": WALLET})
    assert r.status_code == 400 and err(r)["code"] == "UNSUPPORTED_CHAIN"


def test_wallet_query_without_key_is_explicit_degraded_never_silent_fallback(client):
    r = client.post("/api/investigations", json={"chain_id": 1, "address": WALLET})
    e = err(r)
    assert r.status_code == 503 and e["code"] == "PROVIDER_NOT_CONFIGURED" and e["degraded_mode"] is True
    assert "scenario_id" in e["details"]["hint"] and "data_mode" not in r.json()


def test_provider_unavailable_after_retries_returns_503_envelope():
    c = live_client(lambda r: httpx.Response(503), provider_max_retries=1)
    r = c.post("/api/investigations", json={"chain_id": 1, "address": WALLET})
    e = err(r)
    assert r.status_code == 503 and e["code"] == "PROVIDER_RETRY_EXHAUSTED" and e["retryable"] and e["provider"] == "etherscan"
    assert SECRET not in r.text
    assert c.get("/health").json()["degraded_mode"] is True


def test_provider_timeout_with_zero_retries_reports_retry_exhausted():
    def h(r):
        raise httpx.ReadTimeout("x")
    r = live_client(h, provider_max_retries=0).post("/api/investigations", json={"chain_id": 1, "address": WALLET})
    assert r.status_code == 503 and err(r)["code"] == "PROVIDER_RETRY_EXHAUSTED"


def test_live_wallet_investigation_labelled_real_then_cached():
    c = live_client(etherscan_handler(native=[GOOD]))
    body = {"chain_id": 1, "address": WALLET.upper().replace("0X", "0x")}
    a = c.post("/api/investigations", json=body)
    assert a.status_code == 201
    a = a.json()
    assert a["data_mode"] == "real" and a["sources"][0]["provider"] == "etherscan" and a["scope"]["address"] == WALLET.lower()
    b = c.post("/api/investigations", json=body).json()
    assert b["data_mode"] == "cached" and b["investigation_id"] == a["investigation_id"]
    assert any("cache" in l.lower() for l in b["limitations"])
    g = c.get(f"/api/investigations/{b['investigation_id']}/graph").json()
    assert g["data_mode"] == "cached" and g["edges"][0]["provenance"]["data_mode"] == "cached" and len(g["edges"]) == 1
    assert SECRET not in str(a) + str(b) + str(g)


def test_live_wallet_with_no_history_gives_empty_graph():
    c = live_client(etherscan_handler())
    r = c.post("/api/investigations", json={"chain_id": 1, "address": WALLET})
    assert r.status_code == 201 and r.json()["summary"]["edge_count"] == 0 and r.json()["data_cutoff"] is None
    g = c.get(f"/api/investigations/{r.json()['investigation_id']}/graph").json()
    assert g["nodes"] == [] and g["edges"] == [] and g["notes"]


def test_live_malformed_rows_are_reported_not_hidden():
    c = live_client(etherscan_handler(native=[GOOD, {"hash": "bad"}]))
    j = c.post("/api/investigations", json={"chain_id": 1, "address": WALLET}).json()
    assert j["summary"]["event_count"] == 1 and j["summary"]["rejected_rows"] == 1


def test_person2_endpoints_are_implemented_with_shared_metadata(client):
    """Superseded contract: the former 501 stubs are now served by the Person 2 engine."""
    calls = (("get", "leads", None), ("get", "evidence", None), ("post", "sensitivity", {}),
             ("get", "replay?cutoff=2026-10-01T02:30:00Z", None), ("get", "report", None), ("get", "hypotheses", None))
    for method, path, body in calls:
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(f"/api/investigations/{INV}/{path}", **kw)
        assert r.status_code == 200, (path, r.text[:200])
        j = r.json()
        assert META <= set(j) and j["data_mode"] == "synthetic" and j["data_label"] == "SYNTHETIC / SIMULATED"
        assert j["snapshot_id"] and j["config_hash"]


def test_replay_requires_cutoff(client):
    r = client.get(f"/api/investigations/{INV}/replay")
    assert r.status_code == 422 and err(r)["code"] == "CUTOFF_REQUIRED"


def test_openapi_documents_core_contract(client):
    spec = client.get("/openapi.json").json()
    for p in ("/health", "/api/scenarios", "/api/investigations", "/api/investigations/{inv_id}",
              "/api/investigations/{inv_id}/graph", "/api/investigations/{inv_id}/paths"):
        assert p in spec["paths"]
    assert "examples" in spec["components"]["schemas"]["InvestigationCreate"]


def test_data_mode_always_one_of_three(client):
    for path in (f"/api/investigations/{INV}", f"/api/investigations/{INV}/graph", f"/api/investigations/{INV}/paths"):
        assert client.get(path).json()["data_mode"] in {"synthetic", "real", "cached"}


def test_wallet_time_filter_discloses_bounded_sample_limitation():
    c = live_client(etherscan_handler(native=[GOOD]))
    body = {
        "chain_id": 1, "address": WALLET,
        "start_time": "2026-10-01T00:00:00Z",
        "end_time": "2026-10-02T00:00:00Z",
    }
    r = c.post("/api/investigations", json=body)
    assert r.status_code == 201
    j = r.json()
    assert j["scope"]["time_filter_applied_after_fetch"] is True
    assert any("bounded transfer sample" in item for item in j["limitations"])


def test_wallet_without_time_filter_does_not_add_time_sample_warning():
    c = live_client(etherscan_handler(native=[GOOD]))
    r = c.post("/api/investigations", json={"chain_id": 1, "address": WALLET})
    assert r.status_code == 201
    j = r.json()
    assert j["scope"]["time_filter_applied_after_fetch"] is False
    assert not any("bounded transfer sample" in item for item in j["limitations"])
