"""ASTRA Person 2 - data structures and graph algorithms that power the investigation workflow.

Every algorithm here is called by the investigation engine (``astra_intel.engine``); none is a
stand-alone "DSA demo". Graph semantics follow Person 1's canonical transfer schema:

* node  = "<chain_id>:<lowercase address>"
* edge  = ONE observed transfer event (``event_id``); repeated transfers between the same pair are
          kept as separate edges (multigraph), never merged or summed across assets.
* time  = integer UTC epoch seconds of the block timestamp (event time).

Bounds: every traversal takes explicit hop/node/expansion limits and is iterative (no recursion), so
large or adversarial graphs cannot exhaust the Python stack.

Graph connectivity, reachability and value allocation are observed relationships between recorded
transfers. They are NOT proof of common ownership or of the identity of funds.
"""
from __future__ import annotations

import heapq
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable, Optional, Sequence, TypeVar

T = TypeVar("T")


# --------------------------------------------------------------------------------------------
# 1. Graph representation: adjacency lists + hash-map indexes
# --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Edge:
    event_id: str
    u: str            # source node id
    v: str            # target node id
    ts: int           # epoch seconds (event time)
    asset_id: str
    raw: int          # exact integer amount in the asset's smallest unit
    tx_hash: str
    decimals: Optional[int] = None
    symbol: Optional[str] = None

    @property
    def is_self_loop(self) -> bool:
        return self.u == self.v


def node_id(chain_id: int, address: str) -> str:
    return f"{chain_id}:{address.lower()}"


class GraphIndex:
    """Directed multigraph with O(1) average lookups.

    * ``out_adj[node]`` / ``in_adj[node]``: adjacency lists sorted by (ts, event_id)
    * ``by_event[event_id]``: edge lookup
    * ``by_tx_hash[hash]``: all edges of a transaction (a tx may emit several transfer events)
    * ``by_address[address]``: node id lookup from a bare address (chain-agnostic index)

    Build: O(E log E) (sorting adjacency lists). Memory: O(V + E).
    Duplicate event_ids are ignored (counted in ``duplicate_events_ignored``); Person 1's ingestion
    already de-duplicates, this is a defensive second line.
    """

    def __init__(self, edges: Iterable[Edge]):
        self.out_adj: dict[str, list[Edge]] = {}
        self.in_adj: dict[str, list[Edge]] = {}
        self.by_event: dict[str, Edge] = {}
        self.by_tx_hash: dict[str, list[Edge]] = {}
        self.by_address: dict[str, set[str]] = {}
        self.nodes: set[str] = set()
        self.duplicate_events_ignored = 0
        self.self_loops = 0
        for e in edges:
            if e.event_id in self.by_event:
                self.duplicate_events_ignored += 1
                continue
            self.by_event[e.event_id] = e
            self.out_adj.setdefault(e.u, []).append(e)
            self.in_adj.setdefault(e.v, []).append(e)
            self.by_tx_hash.setdefault(e.tx_hash, []).append(e)
            for n in (e.u, e.v):
                if n not in self.nodes:
                    self.nodes.add(n)
                    self.by_address.setdefault(n.split(":", 1)[1], set()).add(n)
            if e.is_self_loop:
                self.self_loops += 1
        key = lambda e: (e.ts, e.event_id)
        for lst in self.out_adj.values():
            lst.sort(key=key)
        for lst in self.in_adj.values():
            lst.sort(key=key)

    # ---- basic queries ----
    @property
    def edge_count(self) -> int:
        return len(self.by_event)

    def out_edges(self, n: str, asset_id: Optional[str] = None) -> list[Edge]:
        lst = self.out_adj.get(n, [])
        return lst if asset_id is None else [e for e in lst if e.asset_id == asset_id]

    def in_edges(self, n: str, asset_id: Optional[str] = None) -> list[Edge]:
        lst = self.in_adj.get(n, [])
        return lst if asset_id is None else [e for e in lst if e.asset_id == asset_id]

    def degree(self, n: str) -> int:
        return len(self.out_adj.get(n, ())) + len(self.in_adj.get(n, ()))

    def repeated_edge_pairs(self) -> int:
        """Number of (u, v, asset) pairs that carry more than one transfer."""
        seen: dict[tuple[str, str, str], int] = {}
        for e in self.by_event.values():
            k = (e.u, e.v, e.asset_id)
            seen[k] = seen.get(k, 0) + 1
        return sum(1 for c in seen.values() if c > 1)

    def assets(self) -> list[str]:
        return sorted({e.asset_id for e in self.by_event.values()})


# --------------------------------------------------------------------------------------------
# 2. BFS / DFS
# --------------------------------------------------------------------------------------------
def bfs_neighborhood(g: GraphIndex, start: str, *, max_hops: int = 2, max_nodes: int = 200,
                     direction: str = "both") -> dict[str, int]:
    """Bounded breadth-first neighbourhood (ignores time and asset): node -> hop distance.

    Powers: the investigation sub-graph returned to the graph view (``/subgraph``) so the UI never
    receives an unbounded graph. Complexity O(V' + E') over the visited part, capped by max_nodes.
    Nodes are expanded in deterministic (hop, node_id) order.
    """
    if start not in g.nodes:
        return {}
    if direction not in {"out", "in", "both"}:
        raise ValueError("direction must be out|in|both")
    dist = {start: 0}
    q = deque([start])
    while q:
        n = q.popleft()
        if dist[n] >= max_hops:
            continue
        nbrs: set[str] = set()
        if direction in ("out", "both"):
            nbrs.update(e.v for e in g.out_adj.get(n, ()))
        if direction in ("in", "both"):
            nbrs.update(e.u for e in g.in_adj.get(n, ()))
        for m in sorted(nbrs):
            if m not in dist:
                if len(dist) >= max_nodes:
                    return dist
                dist[m] = dist[n] + 1
                q.append(m)
    return dist


@dataclass
class Reachability:
    earliest_arrival: dict[str, int]           # node -> earliest time traced value could arrive
    via_edge: dict[str, str]                   # node -> event_id of the edge that set the label
    expansions: int
    truncated: bool


def time_respecting_reachability(g: GraphIndex, start: str, *, start_time: Optional[int] = None,
                                 end_time: Optional[int] = None, asset_id: Optional[str] = None,
                                 max_expansions: int = 500_000) -> Reachability:
    """Iterative depth-first label-correcting search for *time-respecting* reachability.

    A node v is reachable if a chain of transfers start -> ... -> v exists whose timestamps are
    non-decreasing (funds cannot be forwarded before they arrive). Each node keeps its earliest
    arrival time; a node is re-expanded only when a strictly earlier arrival is found, so the
    search terminates on cyclic graphs. Worst case O(E * distinct arrival improvements), bounded by
    ``max_expansions``.

    Powers: candidate-lead selection (who could have received traced value) and the
    "missing link" signal for OSINT-only matches.
    """
    if start not in g.nodes:
        return Reachability({}, {}, 0, False)
    t0 = start_time if start_time is not None else -(1 << 62)
    arrival = {start: t0}
    via: dict[str, str] = {}
    stack = [start]
    expansions = 0
    while stack:
        n = stack.pop()
        t = arrival[n]
        for e in g.out_adj.get(n, ()):
            if e.ts < t or (end_time is not None and e.ts > end_time):
                continue
            if asset_id is not None and e.asset_id != asset_id:
                continue
            expansions += 1
            if expansions > max_expansions:
                return Reachability(arrival, via, expansions, True)
            if e.v not in arrival or e.ts < arrival[e.v]:
                arrival[e.v] = e.ts
                via[e.v] = e.event_id
                stack.append(e.v)
    return Reachability(arrival, via, expansions, False)


def has_directed_cycle(g: GraphIndex) -> bool:
    """Iterative three-colour DFS cycle detection (time ignored). O(V + E).

    Self-loops count as cycles. Used for graph statistics shown in the case overview."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in g.nodes}
    for root in sorted(g.nodes):
        if colour[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        colour[root] = GREY
        while stack:
            n, i = stack[-1]
            outs = g.out_adj.get(n, [])
            if i < len(outs):
                stack[-1] = (n, i + 1)
                m = outs[i].v
                if colour[m] == GREY:
                    return True
                if colour[m] == WHITE:
                    colour[m] = GREY
                    stack.append((m, 0))
            else:
                colour[n] = BLACK
                stack.pop()
    return False


# --------------------------------------------------------------------------------------------
# 3. Shortest paths
# --------------------------------------------------------------------------------------------
@dataclass
class PathResult:
    found: bool
    node_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    hops: int = 0
    cost: Optional[float] = None
    cost_breakdown: list[dict] = field(default_factory=list)
    method: str = ""
    truncated: bool = False


def bfs_min_hops_static(g: GraphIndex, src: str, dst: str, *, max_hops: int = 10) -> PathResult:
    """Unweighted shortest path by hop count on the static directed graph (time ignored).

    Used as the *baseline* reachability in evaluation and to label OSINT-only matches whose only
    connection violates time order. O(V + E)."""
    if src not in g.nodes or dst not in g.nodes:
        return PathResult(False, method="bfs_static")
    if src == dst:
        return PathResult(True, [src], [], 0, method="bfs_static")
    parent: dict[str, tuple[str, str]] = {}
    dist = {src: 0}
    q = deque([src])
    while q:
        n = q.popleft()
        if dist[n] >= max_hops:
            continue
        for e in g.out_adj.get(n, ()):
            if e.v in dist:
                continue
            dist[e.v] = dist[n] + 1
            parent[e.v] = (n, e.event_id)
            if e.v == dst:
                return _unwind(parent, src, dst, "bfs_static")
            q.append(e.v)
    return PathResult(False, method="bfs_static")


def _unwind(parent, src, dst, method) -> PathResult:
    nodes, evs = [dst], []
    while nodes[-1] != src:
        p, eid = parent[nodes[-1]]
        evs.append(eid)
        nodes.append(p)
    nodes.reverse()
    evs.reverse()
    return PathResult(True, nodes, evs, len(evs), method=method)


def min_hop_time_respecting(g: GraphIndex, src: str, *, start_time: Optional[int] = None,
                            max_hops: int = 6, asset_id: Optional[str] = None,
                            targets: Optional[set[str]] = None) -> dict[str, PathResult]:
    """Layered BFS for the minimum number of hops over *time-respecting* paths.

    Layer k holds, for every node, the earliest arrival time achievable with exactly k hops (an
    earlier arrival dominates a later one for the same k, so this DP is exact). A node may re-appear
    in a later layer with an earlier arrival, which is required for correctness on temporal graphs.
    Returns node -> PathResult for every node reached (or only ``targets``). O(max_hops * E).

    Powers: ``incident_path_proximity`` (1 / hops) and the "observed path" evidence of every lead.
    """
    if src not in g.nodes:
        return {}
    t0 = start_time if start_time is not None else -(1 << 62)
    frontier: dict[str, tuple[int, Optional[tuple]]] = {src: (t0, None)}  # node -> (arrival, backptr)
    best_hops: dict[str, tuple[int, tuple]] = {}
    layers: list[dict[str, tuple[int, Optional[tuple]]]] = [frontier]
    for k in range(1, max_hops + 1):
        nxt: dict[str, tuple[int, tuple]] = {}
        for n in sorted(frontier):
            t, _ = frontier[n]
            for e in g.out_adj.get(n, ()):
                if e.ts < t or (asset_id is not None and e.asset_id != asset_id):
                    continue
                cur = nxt.get(e.v)
                if cur is None or e.ts < cur[0] or (e.ts == cur[0] and e.event_id < cur[1][1]):
                    nxt[e.v] = (e.ts, (n, e.event_id, k - 1))
        for v, val in nxt.items():
            if v != src and v not in best_hops:
                best_hops[v] = (k, val)
        layers.append(nxt)
        frontier = nxt
        if not frontier or (targets is not None and targets.issubset(best_hops.keys())):
            break
    out: dict[str, PathResult] = {}
    for v, (k, _) in best_hops.items():
        if targets is not None and v not in targets:
            continue
        nodes, evs, cur, layer = [v], [], v, k
        while layer > 0:
            _, (p, eid, pl) = layers[layer][cur]
            evs.append(eid)
            nodes.append(p)
            cur, layer = p, pl
        nodes.reverse()
        evs.reverse()
        out[v] = PathResult(True, nodes, evs, k, method="bfs_time_respecting")
    return out


def dijkstra_fastest_route(g: GraphIndex, src: str, dst: str, *, start_time: Optional[int] = None,
                           hop_penalty_seconds: float = 3600.0, asset_id: Optional[str] = None,
                           max_expansions: int = 200_000) -> PathResult:
    """Dijkstra over *edge states* with a documented non-negative cost model.

    Cost of a route e1, e2, ..., ek  =  sum_i ( hop_penalty_seconds + dwell_i )
      dwell_1 = 0; dwell_i = e_i.ts - e_{i-1}.ts  (time value waited at the intermediate address)
    Both terms are >= 0 because routes are time-respecting, so Dijkstra is exact. The route with the
    lowest cost is the "most rapidly forwarded" observed route - an investigative prioritisation
    aid. Transaction AMOUNTS are deliberately NOT used as distances.

    State = the last edge taken (a node alone is insufficient because the dwell depends on the
    arrival time). Complexity O(S log S) for S explored states, capped by ``max_expansions``.
    """
    if hop_penalty_seconds < 0:
        raise ValueError("hop_penalty_seconds must be non-negative")
    if src not in g.nodes or dst not in g.nodes or src == dst:
        return PathResult(False, method="dijkstra_fastest")
    t0 = start_time if start_time is not None else -(1 << 62)
    heap: list[tuple[float, str, str]] = []
    best: dict[str, float] = {}
    parent: dict[str, Optional[str]] = {}
    for e in g.out_adj.get(src, ()):
        if e.ts >= t0 and (asset_id is None or e.asset_id == asset_id):
            c = float(hop_penalty_seconds)
            if c < best.get(e.event_id, float("inf")):
                best[e.event_id], parent[e.event_id] = c, None
                heapq.heappush(heap, (c, e.event_id, e.event_id))
    expansions = 0
    done: set[str] = set()
    while heap:
        c, _, eid = heapq.heappop(heap)
        if eid in done:
            continue
        done.add(eid)
        e = g.by_event[eid]
        if e.v == dst:
            chain = [eid]
            while parent[chain[-1]] is not None:
                chain.append(parent[chain[-1]])
            chain.reverse()
            edges = [g.by_event[x] for x in chain]
            breakdown, prev = [], None
            for x in edges:
                dwell = 0 if prev is None else x.ts - prev.ts
                breakdown.append({"event_id": x.event_id, "hop_penalty_s": hop_penalty_seconds, "dwell_s": dwell})
                prev = x
            return PathResult(True, [edges[0].u] + [x.v for x in edges], chain, len(chain), round(c, 3),
                              breakdown, "dijkstra_fastest")
        for f in g.out_adj.get(e.v, ()):
            if f.ts < e.ts or f.event_id in done or (asset_id is not None and f.asset_id != asset_id):
                continue
            expansions += 1
            if expansions > max_expansions:
                return PathResult(False, method="dijkstra_fastest", truncated=True)
            nc = c + hop_penalty_seconds + (f.ts - e.ts)
            if nc < best.get(f.event_id, float("inf")):
                best[f.event_id], parent[f.event_id] = nc, eid
                heapq.heappush(heap, (nc, f.event_id, f.event_id))
    return PathResult(False, method="dijkstra_fastest")


# --------------------------------------------------------------------------------------------
# 4. Top-K with a bounded heap (deterministic ties)
# --------------------------------------------------------------------------------------------
class _Desc:
    """Wrap a comparable so heap order is reversed (for deterministic tie-breaks on strings)."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        return self.v > other.v

    def __eq__(self, other):
        return self.v == other.v


def top_k(items: Iterable[T], k: int, score: Callable[[T], float], tiebreak: Callable[[T], Hashable]) -> list[T]:
    """Select the k highest-scoring items with a size-k min-heap: O(n log k) time, O(k) memory.

    Order: score descending, then ``tiebreak`` ascending, so equal scores are always returned in the
    same order (acceptance check: "Top-K results are deterministic for ties").
    Powers: the ranked lead queue and the evaluation's precision@k.
    """
    if k <= 0:
        return []
    heap: list[tuple[float, _Desc, int, T]] = []
    for i, it in enumerate(items):
        entry = (score(it), _Desc(tiebreak(it)), i, it)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry[:2] > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    heap.sort(key=lambda x: (-x[0], x[1].v))
    return [x[3] for x in heap]


# --------------------------------------------------------------------------------------------
# 5. Flow decomposition (traced value with residuals)
# --------------------------------------------------------------------------------------------
@dataclass
class Parcel:
    amount: int
    path: tuple[str, ...]          # event_ids traversed so far


@dataclass
class FlowDecomposition:
    asset_id: str
    start: str
    initial_raw: int
    path_flows: list[dict]               # terminal parcels aggregated by path
    edge_traced_raw: dict[str, int]      # event_id -> traced portion of that transfer
    edge_untraced_raw: dict[str, int]    # event_id -> portion NOT attributable to traced value
    node_traced_in_raw: dict[str, int]   # node -> total traced value that arrived
    residual_by_node: dict[str, int]     # node -> traced value still at node at the end of the window
    self_loops_skipped: list[str]
    conservation_ok: bool
    truncated: bool
    policy: str = "fifo"


def decompose_flow(g: GraphIndex, start: str, asset_id: str, *, initial_raw: Optional[int] = None,
                   start_time: Optional[int] = None, end_time: Optional[int] = None,
                   max_parcels_per_node: int = 64) -> FlowDecomposition:
    """Chronological FIFO allocation of *traced* value through the transfer graph.

    Assumptions (all reported to the user):
      * The traced amount starts at ``start``; default = the start address's total observed outflow
        of this asset in the window (its observed exposure), never a guessed balance.
      * Each address holds traced value in arrival order; an outgoing transfer of w spends the
        oldest traced parcels first, up to w. Any remainder of w is UNTRACED (other funds).
      * A transfer cannot spend traced value that arrives later (time-respecting by construction).
      * Integer arithmetic only (no float rounding); one asset at a time (no cross-asset sums).
    Output keeps transaction references for every parcel (``path`` = event_ids) and reports the
    value still sitting at each address at the end of the window as an UNRESOLVED residual.
    Conservation invariant: sum(path_flows) == initial_raw (checked and reported).
    Limitations: FIFO is one allocation policy among several (LIFO, pro-rata "haircut"); the
    decomposition shows where value *could* have gone given the observed transfers - it does not
    prove that the same funds moved. Complexity O(E log E + E * P) with P <= max_parcels_per_node.
    """
    evs = sorted((e for e in g.by_event.values() if e.asset_id == asset_id
                  and (start_time is None or e.ts >= start_time) and (end_time is None or e.ts <= end_time)),
                 key=lambda e: (e.ts, e.event_id))
    if initial_raw is None:
        initial_raw = sum(e.raw for e in evs if e.u == start and not e.is_self_loop)
    holdings: dict[str, deque[Parcel]] = {start: deque([Parcel(initial_raw, ())])} if initial_raw > 0 else {}
    traced: dict[str, int] = {}
    untraced: dict[str, int] = {}
    traced_in: dict[str, int] = {}
    loops: list[str] = []
    truncated = False
    for e in evs:
        if e.is_self_loop:
            loops.append(e.event_id)
            continue
        src = holdings.get(e.u)
        remaining = e.raw
        moved: list[Parcel] = []
        while remaining > 0 and src:
            p = src[0]
            take = min(p.amount, remaining)
            if take == p.amount:
                src.popleft()
            else:
                p.amount -= take
            moved.append(Parcel(take, p.path + (e.event_id,)))
            remaining -= take
        got = e.raw - remaining
        if got:
            traced[e.event_id] = got
            traced_in[e.v] = traced_in.get(e.v, 0) + got
            dst = holdings.setdefault(e.v, deque())
            dst.extend(moved)
            if len(dst) > max_parcels_per_node:  # merge the newest overflow parcels, keep provenance flag
                truncated = True
                extra = [dst.pop() for _ in range(len(dst) - max_parcels_per_node + 1)]
                dst.append(Parcel(sum(x.amount for x in extra), extra[-1].path + ("<merged>",)))
        if remaining:
            untraced[e.event_id] = remaining
    agg: dict[tuple[str, tuple[str, ...]], int] = {}
    residual: dict[str, int] = {}
    for n, dq in holdings.items():
        for p in dq:
            if p.amount <= 0:
                continue
            agg[(n, p.path)] = agg.get((n, p.path), 0) + p.amount
            residual[n] = residual.get(n, 0) + p.amount
    flows = [{"terminal_node": n, "event_ids": list(path), "hops": len([x for x in path if x != "<merged>"]),
              "amount_raw": amt, "merged": "<merged>" in path,
              "status": "unresolved_residual" if path else "not_observed_leaving_start"}
             for (n, path), amt in agg.items()]
    flows.sort(key=lambda f: (-f["amount_raw"], f["terminal_node"], f["event_ids"]))
    return FlowDecomposition(asset_id, start, initial_raw, flows, traced, untraced, traced_in, residual, loops,
                             sum(residual.values()) == initial_raw, truncated)


# --------------------------------------------------------------------------------------------
# 6. Union-Find (symmetric grouping only)
# --------------------------------------------------------------------------------------------
class UnionFind:
    """Disjoint-set union with path compression (iterative) and union by size.

    Used ONLY for symmetric relations:
      * weak (undirected) connected components of the transfer graph - a graph statistic and the
        "is this OSINT match connected to the incident at all?" check;
      * OSINT corroboration groups - documents sharing a content hash or publisher are NOT
        independent sources.
    It is never used for directed, time-aware tracing. Amortised ~O(alpha(n)) per operation.
    """

    def __init__(self, items: Iterable[Hashable] = ()):
        self.parent: dict[Hashable, Hashable] = {}
        self.size: dict[Hashable, int] = {}
        for x in items:
            self.add(x)

    def add(self, x: Hashable) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.size[x] = 1

    def find(self, x: Hashable) -> Hashable:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Hashable, b: Hashable) -> Hashable:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self.size[ra] < self.size[rb] or (self.size[ra] == self.size[rb] and str(rb) < str(ra)):
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return ra

    def groups(self) -> dict[Hashable, list[Hashable]]:
        out: dict[Hashable, list[Hashable]] = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return {min(v, key=str): sorted(v, key=str) for v in out.values()}


def weak_components(g: GraphIndex) -> UnionFind:
    uf = UnionFind(g.nodes)
    for e in g.by_event.values():
        uf.union(e.u, e.v)
    return uf


# --------------------------------------------------------------------------------------------
# 7. Supporting metrics / indexes
# --------------------------------------------------------------------------------------------
def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Mid-rank percentile in [0,1] (ties share the average rank). O(n log n)."""
    if not values:
        return {}
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pct = ((i + j) / 2) / (n - 1) if n > 1 else 0.0
        for k in range(i, j + 1):
            out[items[k][0]] = pct
        i = j + 1
    return out


def median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else (s[m - 1] + s[m]) / 2.0
