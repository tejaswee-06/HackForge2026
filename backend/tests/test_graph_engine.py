from datetime import timedelta

import pytest

from app.services.graph_engine.engine import TransferGraph, node_id

X = lambda i: "0x" + "0" * 36 + f"{0xA000 + i:04x}"


@pytest.fixture()
def graph(ingested):
    return TransferGraph(ingested.events)


def sig(paths):
    return {(tuple(p.event_ids), tuple(p.node_ids), p.bottleneck_raw, p.terminated_reason, p.bottleneck_normalized)
            for p in paths}


def exp_sig(plist):
    return {(tuple(p["event_ids"]), tuple(p["node_ids"]), p["bottleneck_raw"], p["terminated_reason"],
             p["bottleneck_normalized"]) for p in plist}


def test_graph_counts_match_oracle(graph, expected):
    c = expected["counts"]
    assert (graph.node_count, graph.edge_count, graph.components()) == (
        c["graph_nodes"], c["graph_edges"], c["weakly_connected_components"])


def test_failed_transaction_excluded_from_graph(graph, expected):
    assert graph.excluded_non_success == expected["excluded_failed_event_ids"]
    assert all(e not in graph.events for e in expected["excluded_failed_event_ids"])


def test_expected_paths_depth6(graph, expected):
    r = graph.trace_paths(1, X(0), max_depth=6, asset_id=expected["native_asset_id"])
    assert r.start_found and sig(r.paths) == exp_sig(expected["paths_native_from_start_depth6"])


def test_split_multi_hop_and_single_hop_present(graph):
    r = graph.trace_paths(1, X(0), max_depth=6, asset_id="1:native")
    assert {p.node_ids[1] for p in r.paths} == {node_id(1, X(1)), node_id(1, X(2))}  # split at X0
    assert max(p.hops for p in r.paths) == 4 and min(p.hops for p in r.paths) == 2  # multi-hop + short
    one = graph.trace_paths(1, X(0), max_depth=1, asset_id="1:native")
    assert all(p.hops == 1 for p in one.paths) and len(one.paths) == 2  # single-hop paths


def test_max_depth_boundary(graph, expected):
    r = graph.trace_paths(1, X(0), max_depth=2, asset_id="1:native")
    assert sig(r.paths) == exp_sig(expected["paths_native_from_start_depth2"])
    assert {p.terminated_reason for p in r.paths} == {"max_depth", "leaf"}
    assert all(p.hops <= 2 for p in r.paths)


def test_cycle_protection(graph):
    r = graph.trace_paths(1, X(0), max_depth=10, asset_id="1:native")
    for p in r.paths:
        assert len(set(p.node_ids)) == len(p.node_ids)  # no wallet repeated
    assert any(p.terminated_reason == "cycle_blocked" for p in r.paths)
    # tracing from inside the cycle terminates and visits each node once
    c = graph.trace_paths(1, X(3), max_depth=10, asset_id="1:native")
    assert c.paths and all(len(set(p.node_ids)) == len(p.node_ids) for p in c.paths)


def test_time_order_enforced_and_optional(graph):
    strict = graph.trace_paths(1, X(0), max_depth=10, asset_id="1:native")
    for p in strict.paths:
        ts = [h.timestamp for h in p.edges]
        assert ts == sorted(ts)
    # From X6: X6->X3 happens at t+20 but X3->X5 happened earlier (t+9).
    ordered = graph.trace_paths(1, X(6), max_depth=10, asset_id="1:native")
    assert [p.hops for p in ordered.paths] == [1] and ordered.paths[0].terminated_reason == "leaf"
    loose = graph.trace_paths(1, X(6), max_depth=10, asset_id="1:native", enforce_time_order=False)
    assert [p.hops for p in loose.paths] == [2] and loose.paths[0].terminated_reason == "cycle_blocked"


def test_disconnected_wallet(graph, expected):
    y1 = expected["disconnected_addresses"][0]
    r = graph.trace_paths(1, X(0), max_depth=10)
    assert all(node_id(1, y1) not in p.node_ids for p in r.paths)
    own = graph.trace_paths(1, y1, max_depth=5)
    assert len(own.paths) == 1 and own.paths[0].bottleneck_raw == str(7 * 10**18)


def test_unknown_start_and_leaf_start(graph):
    assert graph.trace_paths(1, "0x" + "9" * 40).start_found is False
    assert graph.trace_paths(1, X(7), asset_id="1:native").paths == []  # known wallet, no outgoing native edges


def test_duplicate_event_counted_once(graph, expected):
    dup_hash_events = [e for e in graph.events.values() if e.transaction_hash == expected["paths_native_from_start_depth2"][0]["event_ids"][1].split(":")[1]]
    assert len(dup_hash_events) == 1 and graph.edge_count == expected["counts"]["graph_edges"]


def test_result_limit(graph):
    r = graph.trace_paths(1, X(0), max_depth=10, max_results=1, asset_id="1:native")
    assert len(r.paths) == 1 and r.truncated_by_limit is True


def test_asset_filter_and_no_cross_token_chaining(graph, expected):
    tok, nat = expected["token_asset_id"], expected["native_asset_id"]
    assert graph.trace_paths(1, X(0), max_depth=10, asset_id=tok).paths == []  # X0 has no token edges
    r = graph.trace_paths(1, X(2), max_depth=10, asset_id=tok)
    assert sig(r.paths) == exp_sig(expected["paths_token_from_X2"])
    mixed = graph.trace_paths(1, X(2), max_depth=10)  # no filter => each asset traced separately
    assert {p.asset_id for p in mixed.paths} == {tok, nat}
    for p in mixed.paths:
        assert {graph.events[e].asset_id for e in p.event_ids} == {p.asset_id}  # never mixed within a path


def test_time_filter(graph):
    cut = graph.events[next(iter(graph.events))].timestamp  # earliest event time
    r = graph.trace_paths(1, X(0), max_depth=10, asset_id="1:native", end_time=cut + timedelta(minutes=6))
    assert all(p.end_time <= cut + timedelta(minutes=6) for p in r.paths)
    assert graph.trace_paths(1, X(0), max_depth=10, asset_id="1:native", start_time=cut + timedelta(days=1)).paths == []


def test_event_references_resolve(graph):
    for p in graph.trace_paths(1, X(0), max_depth=10).paths:
        for hop, eid in zip(p.edges, p.event_ids):
            ev = graph.events[eid]
            assert hop.event_id == eid and hop.transaction_hash == ev.transaction_hash and hop.raw_value == ev.raw_value


def test_no_double_counting_along_path(graph):
    # path X0->X1->X3->X5->X6 : 100,99,98,120 ETH. Value is NOT summed (would be 417); bottleneck is the min.
    r = graph.trace_paths(1, X(0), max_depth=6, asset_id="1:native")
    long = max(r.paths, key=lambda p: p.hops if p.terminated_reason == "cycle_blocked" else 0)
    assert long.bottleneck_raw == str(98 * 10**18)


def test_gross_totals_per_asset_not_mixed(graph):
    nodes, edges, trunc = graph.export()
    n = {x.node_id: x for x in nodes}
    x4 = n[node_id(1, X(4))]
    assert x4.gross_in_raw == {"1:erc20:0x" + "0" * 36 + "f001": "1000000", "1:native": str(30 * 10**18)}
    assert x4.gross_out_raw == {"1:erc20:0x" + "0" * 36 + "f001": "400000"}
    assert not trunc and len(edges) == 11


def test_export_filters_and_empty(graph):
    n, e, _ = graph.export(asset_id="1:native")
    assert all(x.asset_id == "1:native" for x in e) and len(e) == 9
    n, e, _ = graph.export(asset_id="1:erc20:0x" + "1" * 40)
    assert n == [] and e == []
    n, e, t = graph.export(max_edges=3)
    assert len(e) == 3 and t is True


def test_empty_graph():
    g = TransferGraph([])
    assert (g.node_count, g.edge_count, g.components()) == (0, 0, 0)
    assert g.trace_paths(1, X(0)).start_found is False
    assert g.export() == ([], [], False)


def test_repeated_runs_identical(ingested):
    a = TransferGraph(ingested.events).trace_paths(1, X(0), max_depth=6)
    b = TransferGraph(list(reversed(ingested.events))).trace_paths(1, X(0), max_depth=6)
    assert [p.model_dump_json() for p in a.paths] == [p.model_dump_json() for p in b.paths]
