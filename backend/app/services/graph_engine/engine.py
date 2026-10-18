"""Directed, time-aware transfer graph + bounded path tracing.

* node  = (chain_id, address)  -> id "<chain_id>:<address>"
* edge  = ONE observed transfer event, keyed by event_id (multi-edges are kept, never merged)
* paths are single-asset (unlike tokens are never chained or summed), simple (no repeated node),
  time-ordered by default (each hop must not precede the previous one), depth/result bounded.
* amounts: per-path `bottleneck` = min edge value (an upper bound on traversing value). Values are
  never added along a path, so nothing is double-counted. Node gross_in/out sums are per-asset and
  labelled gross.
Graph connectivity is an observed relationship between recorded transfers - not proof of ownership.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

import networkx as nx

from ...schemas.graph import GraphEdge, GraphNode, PathHop, TracePath
from ...schemas.transaction import NormalizedTransfer, format_units


def node_id(chain_id: int, address: str) -> str:
    return f"{chain_id}:{address.lower()}"


@dataclass
class TraceResult:
    start_found: bool
    paths: list[TracePath] = field(default_factory=list)
    truncated_by_limit: bool = False
    truncated_by_budget: bool = False


class TransferGraph:
    def __init__(self, events: Iterable[NormalizedTransfer], include_non_success: bool = False):
        self.events: dict[str, NormalizedTransfer] = {}
        self.excluded_non_success: list[str] = []
        self.duplicate_event_ids_ignored = 0
        self.g = nx.MultiDiGraph()
        self._out: dict[str, list[NormalizedTransfer]] = {}
        for ev in sorted(events, key=lambda e: (e.timestamp, e.event_id)):
            if ev.event_id in self.events:  # defensive; ingestion already dedups
                self.duplicate_event_ids_ignored += 1
                continue
            if ev.status != "success" and not include_non_success:
                self.excluded_non_success.append(ev.event_id)
                continue
            self.events[ev.event_id] = ev
            u, v = node_id(ev.chain_id, ev.from_address), node_id(ev.chain_id, ev.to_address)
            self.g.add_node(u, chain_id=ev.chain_id, address=ev.from_address)
            self.g.add_node(v, chain_id=ev.chain_id, address=ev.to_address)
            self.g.add_edge(u, v, key=ev.event_id)
            self._out.setdefault(u, []).append(ev)  # already (timestamp, event_id) sorted

    # ---- structure ----
    @property
    def node_count(self) -> int:
        return self.g.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.g.number_of_edges()

    def components(self) -> int:
        return nx.number_weakly_connected_components(self.g) if self.node_count else 0

    def asset_edge_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ev in self.events.values():
            out[ev.asset_id] = out.get(ev.asset_id, 0) + 1
        return dict(sorted(out.items()))

    def has_node(self, chain_id: int, address: str) -> bool:
        return node_id(chain_id, address) in self.g

    # ---- filtered export ----
    def filtered_events(self, asset_id: Optional[str] = None, start: Optional[datetime] = None,
                        end: Optional[datetime] = None) -> list[NormalizedTransfer]:
        return [e for e in sorted(self.events.values(), key=lambda e: (e.timestamp, e.event_id))
                if (asset_id is None or e.asset_id == asset_id)
                and (start is None or e.timestamp >= start) and (end is None or e.timestamp <= end)]

    def export(self, asset_id=None, start=None, end=None, max_edges: int = 2000):
        evs = self.filtered_events(asset_id, start, end)
        truncated = len(evs) > max_edges
        evs = evs[:max_edges]
        nodes: dict[str, dict] = {}
        for e in evs:
            for nid, addr, direction in ((node_id(e.chain_id, e.from_address), e.from_address, "out"),
                                         (node_id(e.chain_id, e.to_address), e.to_address, "in")):
                n = nodes.setdefault(nid, {"chain_id": e.chain_id, "address": addr, "first": e.timestamp,
                                           "last": e.timestamp, "in": 0, "out": 0, "gin": {}, "gout": {}})
                n["first"], n["last"] = min(n["first"], e.timestamp), max(n["last"], e.timestamp)
                n[direction] += 1
                bucket = n["gin"] if direction == "in" else n["gout"]
                bucket[e.asset_id] = bucket.get(e.asset_id, 0) + int(e.raw_value)
        node_models = [GraphNode(node_id=k, chain_id=v["chain_id"], address=v["address"], first_seen=v["first"],
                                 last_seen=v["last"], in_transfer_count=v["in"], out_transfer_count=v["out"],
                                 gross_in_raw={a: str(x) for a, x in sorted(v["gin"].items())},
                                 gross_out_raw={a: str(x) for a, x in sorted(v["gout"].items())})
                       for k, v in sorted(nodes.items())]
        edge_models = [GraphEdge(edge_id=e.event_id, source=node_id(e.chain_id, e.from_address),
                                 target=node_id(e.chain_id, e.to_address), chain_id=e.chain_id,
                                 transaction_hash=e.transaction_hash, log_index=e.log_index,
                                 block_number=e.block_number, timestamp=e.timestamp, asset_id=e.asset_id,
                                 asset_type=e.asset_type, asset_symbol=e.asset_symbol,
                                 token_contract=e.token_contract, token_decimals=e.token_decimals,
                                 raw_value=e.raw_value, normalized_value=e.normalized_value, status=e.status,
                                 event_type=e.event_type, provenance=e.source) for e in evs]
        return node_models, edge_models, truncated

    # ---- bounded path tracing ----
    def trace_paths(self, chain_id: int, start_address: str, *, max_depth: int = 4, max_results: int = 50,
                    asset_id: Optional[str] = None, start_time: Optional[datetime] = None,
                    end_time: Optional[datetime] = None, enforce_time_order: bool = True,
                    max_expansions: int = 200_000) -> TraceResult:
        start = node_id(chain_id, start_address)
        if start not in self.g:
            return TraceResult(start_found=False)
        assets = [asset_id] if asset_id else sorted({e.asset_id for e in self.events.values()})
        res = TraceResult(start_found=True)
        expansions = 0
        for asset in assets:
            queue: deque[tuple[tuple[str, ...], tuple[NormalizedTransfer, ...]]] = deque([((start,), ())])
            while queue:
                nodes, evs = queue.popleft()
                last = nodes[-1]
                floor = evs[-1].timestamp if (evs and enforce_time_order) else None
                cands = [e for e in self._out.get(last, [])
                         if e.asset_id == asset
                         and (start_time is None or e.timestamp >= start_time)
                         and (end_time is None or e.timestamp <= end_time)
                         and (floor is None or e.timestamp >= floor)]
                free = [e for e in cands if node_id(e.chain_id, e.to_address) not in nodes]
                depth = len(evs)
                if evs:
                    reason = None
                    if not cands:
                        reason = "leaf"
                    elif not free:
                        reason = "cycle_blocked"
                    elif depth >= max_depth:
                        reason = "max_depth"
                    if reason:
                        res.paths.append(self._make_path(asset, nodes, evs, reason))
                        if len(res.paths) >= max_results:
                            res.truncated_by_limit = bool(queue) or asset != assets[-1]
                            return res
                        continue
                for e in free:
                    expansions += 1
                    if expansions > max_expansions:
                        res.truncated_by_budget = True
                        return res
                    queue.append((nodes + (node_id(e.chain_id, e.to_address),), evs + (e,)))
        return res

    @staticmethod
    def _make_path(asset: str, nodes, evs, reason: str) -> TracePath:
        ids = [e.event_id for e in evs]
        bottleneck = min(int(e.raw_value) for e in evs)
        decs = {e.token_decimals for e in evs}
        norm = format_units(bottleneck, decs.pop()) if len(decs) == 1 and None not in decs else None
        return TracePath(
            path_id=hashlib.sha1("|".join(ids).encode()).hexdigest()[:12], asset_id=asset,
            node_ids=list(nodes), event_ids=ids, hops=len(evs), start_time=evs[0].timestamp,
            end_time=evs[-1].timestamp, bottleneck_raw=str(bottleneck), bottleneck_normalized=norm,
            terminated_reason=reason,
            edges=[PathHop(event_id=e.event_id, from_node=node_id(e.chain_id, e.from_address),
                           to_node=node_id(e.chain_id, e.to_address), transaction_hash=e.transaction_hash,
                           log_index=e.log_index, timestamp=e.timestamp, raw_value=e.raw_value,
                           normalized_value=e.normalized_value) for e in evs])
