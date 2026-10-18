"""Engine invariants on Person 1's canonical scenario + its synthetic OSINT fixture."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.ingestion.service import ingest_scenario, load_scenarios
from app.services.osint_ingest.service import load_fixture_documents
from astra_intel.engine import AnalysisConfig, Overrides, analyze, compare_runs
from astra_intel.report import build_report, render_html

DATA = Path(__file__).resolve().parents[3] / "data"
T0 = datetime(2026, 10, 1, 2, 14, tzinfo=timezone.utc)
X = lambda n: "0x" + "0" * 36 + f"{0xA000 + n:04x}"


@pytest.fixture(scope="module")
def case():
    sc = load_scenarios(DATA)["ghost_attacks_synthetic_v1"]
    return sc, ingest_scenario(sc), load_fixture_documents(DATA, sc.scenario_id).engine_documents()


def run(case, **kw):
    sc, ing, docs = case
    return analyze(investigation_id="inv-t", chain_id=1, start_address=sc.start_address, events=ing.events,
                   documents=kw.pop("documents", docs), data_mode="synthetic", dataset_ref="t", **kw)


def lead(a, n):
    return next(L for L in a["leads"] if L["address"] == X(n))


def test_deterministic_and_reproducible(case):
    a, b = run(case), run(case)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert a["meta"]["snapshot_id"] == b["meta"]["snapshot_id"]


def test_candidates_exclude_origin_and_failed_and_disconnected(case):
    a = run(case)
    addrs = {L["address"] for L in a["leads"]}
    assert X(0) not in addrs  # origin
    assert "0x" + "0" * 36 + "b001" not in addrs  # disconnected pair
    assert X(7) in addrs  # reachable only through the ERC-20 token leg (X2 -> X4 -> X7)
    assert a["graph_stats"]["has_directed_cycle"] and a["graph_stats"]["weak_components"] == 2
    assert all(f["conservation_ok"] for f in a["flow"])


def test_every_lead_is_explained_and_scores_are_not_probabilities(case):
    a = run(case)
    for L in a["leads"]:
        assert L["score_type"] == "transparent_priority_score_not_probability"
        assert L["reasons"] and L["components"] and L["next_check"]
        assert "model_illicit_score" in L["not_applicable_signals"]
        assert 0 <= L["priority_score"] <= 100
    ranks = [L["rank"] for L in a["leads"]]
    assert ranks == list(range(1, len(ranks) + 1))
    scores = [L["priority_score"] for L in a["leads"]]
    assert scores == sorted(scores, reverse=True)


def test_missing_osint_is_missing_not_negative(case):
    a = run(case, documents=[])
    L = a["leads"][0]
    assert "osint.indicator_match" in L["missing_signals"]
    assert all(c["points"] >= 0 for c in L["components"] if c["signal"].startswith("osint."))
    # removing all public sources can only lower or keep priority (no OSINT penalty is invented)
    base = {L["address"]: L["priority_score"] for L in run(case)["leads"] if not L["contradiction_evidence_ids"]}
    for L in a["leads"]:
        if L["address"] in base:
            assert L["priority_score"] <= base[L["address"]] + 1e-9


def test_contradiction_is_visible_and_penalised(case):
    a = run(case)
    x5 = lead(a, 5)
    assert x5["status"] == "disputed" and x5["contradiction_evidence_ids"]
    pen = next(c for c in x5["components"] if c["signal"] == "evidence.contradiction_strength")
    assert pen["points"] < 0
    claim = next(c for c in a["claims"] if c["claim_id"] == f"CLM-{x5['lead_id']}-OSINT")
    assert claim["status"] == "Disputed" and claim["support_evidence_ids"] and claim["contradiction_evidence_ids"]


def test_claim_evidence_links_are_bidirectional(case):
    a = run(case)
    ev = {e["evidence_id"]: e for e in a["evidence"]}
    for c in a["claims"]:
        for k in ("support_evidence_ids", "contradiction_evidence_ids", "context_evidence_ids"):
            for eid in c[k]:
                assert c["claim_id"] in ev[eid]["claim_ids"]
    for e in a["evidence"]:
        for cid in e["claim_ids"]:
            c = next(c for c in a["claims"] if c["claim_id"] == cid)
            assert e["evidence_id"] in c["support_evidence_ids"] + c["contradiction_evidence_ids"] + c["context_evidence_ids"]


def test_osint_evidence_is_traceable_to_source_records(case):
    a = run(case)
    docs = {d["doc_id"]: d for d in a["osint"]["documents"]}
    for e in a["evidence"]:
        if e["kind"] == "osint_indicator":
            assert e["refs"]["doc_id"] in docs and e["provenance"]["source_url"].startswith("https://")
            assert e["provenance"]["excerpt"] and e["provenance"]["data_mode"] == "synthetic"
    assert a["osint"]["match_summary"]["ambiguous"] == 1 and a["osint"]["documents_unusable"][0]["status"] == "failed"


def test_duplicate_mirror_is_not_independent_corroboration(case):
    a = run(case)
    x1 = lead(a, 1)
    assert len(x1["osint"]["independent_groups"]) == 1  # report + identical mirror = one group
    mirror_ev = [e for e in a["evidence"] if e["entity"] == x1["node_id"] and e["kind"] == "osint_indicator"]
    assert sum(1 for e in mirror_ev if e["hypothesis_relations"]["H1"]["direction"] == "supports") == 1


def test_sensitivity_recomputes_through_engine(case):
    a = run(case)
    top = a["leads"][0]
    ev_id = next(e for e in top["context_evidence_ids"] + top["support_evidence_ids"] if e.startswith("EV-TEMP-"))
    b = run(case, overrides=Overrides(remove_evidence_ids=[ev_id]))
    cmp = compare_runs(a, b)
    row = next(r for r in cmp["leads"] if r["lead_id"] == top["lead_id"])
    assert row["score_after"] < row["score_before"]
    assert any(o["signal"] == "temporal.rapid_forwarding" for o in next(L for L in b["leads"] if L["lead_id"] == top["lead_id"])["signal_overrides"])
    d = run(case, overrides=Overrides(downgrade_evidence_ids=[ev_id], downgrade_factor=0.5))
    sd = next(L for L in d["leads"] if L["lead_id"] == top["lead_id"])["priority_score"]
    assert row["score_after"] <= sd <= row["score_before"]


def test_removing_a_source_document_changes_findings(case):
    a = run(case)
    label_doc = next(d["doc_id"] for d in a["osint"]["documents"] if d["source_type"] == "label_registry")
    b = run(case, overrides=Overrides(remove_doc_ids=[label_doc]))
    assert lead(a, 5)["status"] == "disputed" and lead(b, 5)["status"] != "disputed"
    assert lead(b, 5)["priority_score"] > lead(a, 5)["priority_score"]


def test_weight_override_changes_ranking_inputs(case):
    a = run(case)
    b = run(case, config=AnalysisConfig(weights={"temporal.rapid_forwarding": 0.0}))
    assert a["meta"]["config_hash"] != b["meta"]["config_hash"]
    assert lead(b, 2)["weights_used"]["temporal.rapid_forwarding"] == 0.0
    with pytest.raises(ValueError):
        run(case, config=AnalysisConfig(weights={"no.such": 1.0}))


@pytest.mark.parametrize("minutes", [0, 5, 9, 12, 16, 30])
def test_replay_has_no_future_leakage(case, minutes):
    cutoff = T0 + timedelta(minutes=minutes)
    a = run(case, cutoff=cutoff)
    c = int(cutoff.timestamp())
    sc, ing, docs = case
    ts = {e.event_id: int(e.timestamp.timestamp()) for e in ing.events}
    for L in a["leads"]:
        for eid in (L["path"] or {}).get("event_ids", []):
            assert ts[eid] <= c
    for e in a["evidence"]:
        for eid in e["refs"].get("event_ids", []):
            assert ts[eid] <= c
    for d in a["osint"]["documents"]:
        assert datetime.fromisoformat(str(d["retrieved_at"]).replace("Z", "+00:00")) <= cutoff
    assert all(t["known_at_cutoff"] == ((t["time"] or "9") <= cutoff.isoformat().replace("+00:00", "Z")) for t in a["timeline"]
               if t["kind"] == "transfer")


def test_replay_policy_publication_vs_observation(case):
    cutoff = T0 + timedelta(minutes=47)  # report published at +45 min, retrieved at +50 min
    obs = run(case, cutoff=cutoff)
    pub = run(case, cutoff=cutoff, config=AnalysisConfig(replay_policy="publication"))
    assert len(obs["osint"]["documents"]) == 0
    assert any(d["source_type"] == "threat_report" for d in pub["osint"]["documents"])
    # label registry was published 30 days before but retrieved later: only the publication policy sees it
    assert any(d["source_type"] == "label_registry" for d in pub["osint"]["documents"])


def test_replay_changes_only_through_computation(case):
    early = run(case, cutoff=T0 + timedelta(minutes=6))
    late = run(case, cutoff=T0 + timedelta(hours=6))
    full = run(case)
    assert len(early["leads"]) < len(late["leads"])
    assert [L["lead_id"] for L in late["leads"]] == [L["lead_id"] for L in full["leads"]]


def test_empty_and_missing_origin_give_h3_not_errors():
    a = analyze(investigation_id="e", chain_id=1, start_address=X(0), events=[], documents=[], data_mode="real", dataset_ref="e")
    assert a["leads"] == [] and a["case_hypotheses"]["leading"] == "H3"
    assert any(b["type"] == "origin_not_in_graph" for b in a["boundaries"]) and a["meta"]["data_label"] == "LIVE"


def test_report_matches_analysis_and_escapes_html(case):
    sc, ing, docs = case
    evil = copy.deepcopy(docs)
    evil[0]["text"] += ' <script>alert("x")</script> attacker ' + X(3)
    a = run(case, documents=evil)
    rep = build_report(a, {"investigation_id": "inv-t", "chain_id": 1}, generated_at="2026-10-17T00:00:00Z")
    assert [L["lead_id"] for L in rep["leads"]] == [L["lead_id"] for L in a["leads"][:10]]
    assert rep["analysis"]["snapshot_id"] == a["meta"]["snapshot_id"] and rep["synthetic"]
    html = render_html(rep)
    assert "<script>" not in html and "&lt;script&gt;" in html and "SYNTHETIC / SIMULATED" in html


def test_config_validation():
    for bad in (dict(max_hops=0), dict(top_k=0), dict(replay_policy="future"), dict(rapid_window_seconds=-1)):
        with pytest.raises(ValueError):
            AnalysisConfig(**bad).validate()
    with pytest.raises(ValueError):
        analyze(investigation_id="e", chain_id=1, start_address=None, events=[], documents=[], data_mode="real",
                dataset_ref="e", overrides=Overrides(downgrade_factor=2))
