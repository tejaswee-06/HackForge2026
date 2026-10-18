"""Simulation: deterministic, labelled, and analysed through the real normalizer + engine."""
import json

from app.schemas.intel import SimulationCreate
from app.services.evaluation import baseline_ranking, run_benchmark, run_scenario
from astra_intel.simulation import generate


def cfg(**kw):
    return SimulationCreate(**kw).model_dump()


def test_same_config_same_records_and_results():
    a, b = generate(cfg(seed=11)), generate(cfg(seed=11))
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    _, ra, _ = run_scenario(cfg(seed=11))
    _, rb, _ = run_scenario(cfg(seed=11))
    assert [L["lead_id"] for L in ra["leads"]] == [L["lead_id"] for L in rb["leads"]]
    assert ra["meta"]["snapshot_id"] == rb["meta"]["snapshot_id"]


def test_different_seed_changes_data():
    a, b = generate(cfg(seed=1)), generate(cfg(seed=2))
    assert a["start_address"] != b["start_address"] and a["native_rows"] != b["native_rows"]


def test_generated_data_exercises_edge_cases_and_is_labelled():
    g, res, nb = run_scenario(cfg(seed=3))
    assert nb.duplicates_dropped >= 2 and not nb.rejected
    assert any(e.status == "failed" for e in nb.events)
    assert res["graph_stats"]["has_directed_cycle"] and res["graph_stats"]["self_loops"] >= 1
    assert res["graph_stats"]["weak_components"] >= 3 and res["graph_stats"]["repeated_edge_pairs"] >= 1
    assert len(res["graph_stats"]["assets"]) == 2
    assert res["meta"]["data_label"] == "SYNTHETIC / SIMULATED" and res["limitations"][0].startswith("SYNTHETIC")
    assert all(d["url"].startswith("https://synthetic.astra.invalid/") for d in g["documents"])
    kinds = res["osint"]["match_summary"]
    assert kinds.get("exact") and kinds.get("ambiguous") and kinds.get("strong")
    assert any(f["contradictory"] for f in res["osint"]["findings"])
    assert res["osint"]["documents_unusable"]  # simulated outage is reported, not hidden


def test_ground_truth_never_reaches_the_engine_output():
    g, res, _ = run_scenario(cfg(seed=5))
    blob = json.dumps(res, default=str)
    assert "ground_truth" not in blob and "relevant_addresses" not in blob and "expected_case_hypothesis" not in blob


def test_scenario_kinds_produce_outcomes_through_computation():
    out = {k: run_scenario(cfg(seed=9, scenario_kind=k))[1]["case_hypotheses"]["leading"]
           for k in ("incident", "benign", "insufficient")}
    assert out == {"incident": "H1", "benign": "H2", "insufficient": "H3"}


def test_removing_evidence_moves_incident_case_toward_h3():
    # the same incident generator without public sources and with slow forwarding is not decisive
    g, res, _ = run_scenario(cfg(seed=9, osint_incident_report=False, osint_contradiction=False, osint_service_label=False,
                                 osint_missing_timestamp=False, osint_ambiguous=False, osint_duplicate=False))
    assert res["case_hypotheses"]["leading"] in ("H1", "H3")
    assert res["case_hypotheses"]["hypotheses"][2]["score"] > run_scenario(cfg(seed=9))[1]["case_hypotheses"]["hypotheses"][2]["score"]


def test_service_hubs_are_pushed_down_relative_to_baseline():
    g, res, nb = run_scenario(cfg(seed=4))
    svc = set(g["ground_truth"]["service_addresses"])
    astra_rank = min(i for i, L in enumerate(res["leads"]) if L["address"] in svc)
    base = baseline_ranking(nb.events, g["start_address"])
    base_rank = min(i for i, a in enumerate(base) if a in svc)
    assert astra_rank > base_rank


def test_benchmark_reports_denominators_and_baseline():
    b = run_benchmark([1, 2], ks=(5,))
    agg = b["aggregate"]
    assert agg["incident_scenarios"] == 2 and agg["benign_scenarios"] == 2 and agg["case_hypothesis_n"] == 6
    assert 0 <= agg["astra_precision_at_5"] <= 1 and 0 <= agg["baseline_precision_at_5"] <= 1
    assert b["benchmark_type"].startswith("controlled_synthetic") and b["limitations"]


def test_randomised_configs_never_crash_and_keep_invariants():
    import random
    from datetime import datetime, timezone
    from astra_intel.engine import analyze
    rnd = random.Random(1234)
    for i in range(25):
        c = cfg(seed=rnd.randrange(10 ** 6), scenario_kind=rnd.choice(["incident", "benign", "insufficient"]),
                depth=rnd.randint(1, 6), branching=rnd.randint(1, 5), noise_addresses=rnd.randint(0, 30),
                noise_transfers=rnd.randint(0, 60), disconnected_components=rnd.randint(0, 3),
                include_cycle=rnd.random() < .5, include_self_loop=rnd.random() < .5,
                duplicate_records=rnd.randint(0, 3), failed_transactions=rnd.randint(0, 2),
                include_token_leg=rnd.random() < .5, osint_ambiguous=rnd.random() < .5, osint_outage=rnd.random() < .5)
        g, res, nb = run_scenario(c)
        assert not nb.rejected, (i, nb.rejected[:2])
        assert all(f["conservation_ok"] for f in res["flow"])
        assert res["case_hypotheses"]["leading"] in ("H1", "H2", "H3")
        ranks = [L["rank"] for L in res["leads"]]
        assert ranks == list(range(1, len(ranks) + 1))
        times = sorted(t["time"] for t in res["timeline"] if t["kind"] == "transfer")
        if times:
            cut = datetime.fromisoformat(times[len(times) // 2].replace("Z", "+00:00"))
            docs = []  # transfers-only replay invariant
            r = analyze(investigation_id="f", chain_id=1, start_address=g["start_address"], events=nb.events,
                        documents=docs, data_mode="synthetic", dataset_ref="f", cutoff=cut)
            assert r["graph_stats"]["events_excluded_by_cutoff"] >= 0
            assert all(t["time"] <= times[len(times) // 2] for t in r["timeline"] if t["kind"] == "transfer" and t["known_at_cutoff"])
