"""DSA unit tests on small deterministic fixtures: cycles, self-loops, repeated edges, disconnected
components, empty graphs, time-window boundaries and deterministic ties."""
import random

import pytest

from astra_intel import dsa
from astra_intel.dsa import Edge, GraphIndex


def E(eid, u, v, ts, raw=10, asset="1:native", tx=None):
    return Edge(eid, f"1:{u}", f"1:{v}", ts, asset, raw, tx or f"0x{eid}")


def N(x):
    return f"1:{x}"


@pytest.fixture()
def g():
    #  A ->B (t1) ; B->C (t5) ; A->D (t2) ; D->C (t3) ; C->A (t9, cycle) ; B->B self-loop ; A->B repeated (t4)
    #  X->Y disconnected ; C->E (t0 - before C receives: not time-respecting)
    return GraphIndex([E("e1", "a", "b", 1), E("e2", "b", "c", 5), E("e3", "a", "d", 2), E("e4", "d", "c", 3),
                       E("e5", "c", "a", 9), E("e6", "b", "b", 6), E("e7", "a", "b", 4, raw=3), E("e8", "x", "y", 1),
                       E("e9", "c", "e", 0)])


def test_index_hash_maps_and_counts(g):
    assert g.edge_count == 9 and len(g.nodes) == 7
    assert [e.event_id for e in g.out_adj[N("a")]] == ["e1", "e3", "e7"]  # sorted by time
    assert g.by_tx_hash["0xe2"][0].v == N("c") and N("b") in g.by_address["b"]
    assert g.self_loops == 1 and g.repeated_edge_pairs() == 1


def test_duplicate_event_ids_are_ignored_not_double_counted():
    g = GraphIndex([E("e1", "a", "b", 1), E("e1", "a", "b", 1)])
    assert g.edge_count == 1 and g.duplicate_events_ignored == 1


def test_empty_graph_everything_is_safe():
    g = GraphIndex([])
    assert dsa.bfs_neighborhood(g, N("a")) == {}
    assert dsa.time_respecting_reachability(g, N("a")).earliest_arrival == {}
    assert dsa.min_hop_time_respecting(g, N("a")) == {}
    assert not dsa.dijkstra_fastest_route(g, N("a"), N("b")).found
    assert not dsa.has_directed_cycle(g)
    assert dsa.top_k([], 3, score=lambda x: x, tiebreak=lambda x: x) == []
    f = dsa.decompose_flow(g, N("a"), "1:native")
    assert f.initial_raw == 0 and f.path_flows == [] and f.conservation_ok


def test_bfs_neighbourhood_is_bounded(g):
    d = dsa.bfs_neighborhood(g, N("a"), max_hops=1, direction="out")
    assert d == {N("a"): 0, N("b"): 1, N("d"): 1}
    d2 = dsa.bfs_neighborhood(g, N("a"), max_hops=5, max_nodes=3)
    assert len(d2) == 3
    assert N("x") not in dsa.bfs_neighborhood(g, N("a"), max_hops=10)  # disconnected component
    with pytest.raises(ValueError):
        dsa.bfs_neighborhood(g, N("a"), direction="sideways")


def test_time_respecting_reachability_handles_cycles_and_time_order(g):
    r = dsa.time_respecting_reachability(g, N("a"), start_time=0)
    assert set(r.earliest_arrival) == {N("a"), N("b"), N("c"), N("d")}
    assert N("e") not in r.earliest_arrival  # C->E happens before C receives anything
    assert r.earliest_arrival[N("c")] == 3  # via D (t3), earlier than via B (t5)
    assert not r.truncated


def test_reachability_respects_time_window_boundaries(g):
    # end_time is inclusive: edges at exactly end_time are allowed
    r = dsa.time_respecting_reachability(g, N("a"), start_time=0, end_time=3)
    assert set(r.earliest_arrival) == {N("a"), N("b"), N("c"), N("d")}
    r2 = dsa.time_respecting_reachability(g, N("a"), start_time=0, end_time=2)
    assert N("c") not in r2.earliest_arrival
    r3 = dsa.time_respecting_reachability(g, N("a"), start_time=2)  # start_time inclusive
    assert N("d") in r3.earliest_arrival and r3.earliest_arrival[N("b")] == 4


def test_expansion_budget_truncates():
    edges = [E(f"e{i}", f"n{i}", f"n{i + 1}", i) for i in range(50)]
    r = dsa.time_respecting_reachability(GraphIndex(edges), N("n0"), max_expansions=10)
    assert r.truncated and len(r.earliest_arrival) <= 12


def test_min_hop_time_respecting_requires_revisiting_with_earlier_arrival():
    # 2-hop path to M arrives late (t10) and cannot continue; a 3-hop path arrives early (t3) and can.
    edges = [E("s1", "s", "m", 10), E("s2", "s", "p", 1), E("s3", "p", "q", 2), E("s4", "q", "m", 3),
             E("s5", "m", "t", 5)]
    g = GraphIndex(edges)
    res = dsa.min_hop_time_respecting(g, N("s"), start_time=0, max_hops=6)
    assert res[N("m")].hops == 1
    assert res[N("t")].hops == 4 and res[N("t")].event_ids == ["s2", "s3", "s4", "s5"]
    # static BFS ignores time and finds an impossible 2-hop route
    st = dsa.bfs_min_hops_static(g, N("s"), N("t"))
    assert st.hops == 2 and st.event_ids == ["s1", "s5"]


def test_min_hop_respects_max_hops(g):
    res = dsa.min_hop_time_respecting(g, N("a"), start_time=0, max_hops=1)
    assert set(res) == {N("b"), N("d")}


def test_dijkstra_cost_model_prefers_fast_forwarding():
    # route 1: a->b (t0), b->t (t10000): dwell 10000 s ; route 2: a->c (t5), c->d (t6), d->t (t7): 3 hops, dwell 2 s
    edges = [E("r1", "a", "b", 0), E("r2", "b", "t", 10000), E("q1", "a", "c", 5), E("q2", "c", "d", 6), E("q3", "d", "t", 7)]
    g = GraphIndex(edges)
    r = dsa.dijkstra_fastest_route(g, N("a"), N("t"), hop_penalty_seconds=3600)
    assert r.found and r.event_ids == ["q1", "q2", "q3"] and r.cost == 3 * 3600 + 2
    assert all(b["dwell_s"] >= 0 for b in r.cost_breakdown)
    # with a large hop penalty the 2-hop route wins
    r2 = dsa.dijkstra_fastest_route(g, N("a"), N("t"), hop_penalty_seconds=100000)
    assert r2.event_ids == ["r1", "r2"]
    with pytest.raises(ValueError):
        dsa.dijkstra_fastest_route(g, N("a"), N("t"), hop_penalty_seconds=-1)


def test_dijkstra_never_uses_time_violating_routes(g):
    r = dsa.dijkstra_fastest_route(g, N("a"), N("e"), start_time=0)
    assert not r.found


def test_top_k_is_heap_equivalent_to_sort_and_deterministic_for_ties():
    rnd = random.Random(7)
    items = [(f"id{i:03d}", rnd.choice([1.0, 2.0, 3.0, 2.5])) for i in range(200)]
    top = dsa.top_k(items, 15, score=lambda x: x[1], tiebreak=lambda x: x[0])
    ref = sorted(items, key=lambda x: (-x[1], x[0]))[:15]
    assert top == ref
    shuffled = items[:]
    rnd.shuffle(shuffled)
    assert dsa.top_k(shuffled, 15, score=lambda x: x[1], tiebreak=lambda x: x[0]) == ref
    assert dsa.top_k(items, 0, score=lambda x: x[1], tiebreak=lambda x: x[0]) == []
    assert len(dsa.top_k(items, 500, score=lambda x: x[1], tiebreak=lambda x: x[0])) == 200


def test_flow_decomposition_conserves_value_keeps_refs_and_residuals():
    # S sends 100 to A (t1) and 50 to B (t2); A forwards 60 to C (t3) and 70 to D (t4, only 40 traced left)
    edges = [E("f1", "s", "a", 1, 100), E("f2", "s", "b", 2, 50), E("f3", "a", "c", 3, 60), E("f4", "a", "d", 4, 70),
             E("f5", "c", "c", 5, 10), E("f6", "b", "a", 0, 999)]  # f6 is before B receives anything
    g = GraphIndex(edges)
    f = dsa.decompose_flow(g, N("s"), "1:native")
    assert f.initial_raw == 150 and f.conservation_ok
    assert sum(p["amount_raw"] for p in f.path_flows) == 150
    assert f.edge_traced_raw["f4"] == 40 and f.edge_untraced_raw["f4"] == 30
    assert f.residual_by_node[N("c")] == 60 and f.residual_by_node[N("d")] == 40 and f.residual_by_node[N("b")] == 50
    assert {tuple(p["event_ids"]) for p in f.path_flows} == {("f1", "f3"), ("f1", "f4"), ("f2",)}
    assert f.self_loops_skipped == ["f5"] and "f6" not in f.edge_traced_raw  # time-respecting


def test_flow_decomposition_never_mixes_assets():
    edges = [E("a1", "s", "a", 1, 100), E("t1", "s", "a", 2, 7, asset="1:erc20:0xt")]
    g = GraphIndex(edges)
    f = dsa.decompose_flow(g, N("s"), "1:native")
    assert f.initial_raw == 100 and all(eid != "t1" for p in f.path_flows for eid in p["event_ids"])


def test_flow_decomposition_through_cycle_terminates_and_conserves(g):
    f = dsa.decompose_flow(g, N("a"), "1:native", start_time=0)
    assert f.conservation_ok


def test_union_find_components_and_groups(g):
    uf = dsa.weak_components(g)
    assert uf.find(N("a")) == uf.find(N("c")) == uf.find(N("e"))
    assert uf.find(N("x")) == uf.find(N("y")) != uf.find(N("a"))
    assert len(uf.groups()) == 2
    u = dsa.UnionFind(range(1000))
    for i in range(999):
        u.union(i, i + 1)
    assert len(u.groups()) == 1 and u.size[u.find(0)] == 1000


def test_cycle_detection_and_metrics(g):
    assert dsa.has_directed_cycle(g)
    acyclic = GraphIndex([E("1", "a", "b", 1), E("2", "b", "c", 2)])
    assert not dsa.has_directed_cycle(acyclic)
    assert dsa.has_directed_cycle(GraphIndex([E("1", "a", "a", 1)]))
    p = dsa.percentile_ranks({"a": 1, "b": 2, "c": 2, "d": 9})
    assert p["a"] == 0.0 and p["b"] == p["c"] == 0.5 and p["d"] == 1.0
    assert dsa.median([3, 1, 2]) == 2.0 and dsa.median([]) is None


def test_deep_chain_has_no_recursion_limit():
    edges = [E(f"e{i}", f"n{i}", f"n{i + 1}", i) for i in range(5000)]
    g = GraphIndex(edges)
    r = dsa.time_respecting_reachability(g, N("n0"))
    assert len(r.earliest_arrival) == 5001
    assert not dsa.has_directed_cycle(g)
    f = dsa.decompose_flow(g, N("n0"), "1:native")
    assert f.conservation_ok and f.residual_by_node[N("n5000")] == 10
