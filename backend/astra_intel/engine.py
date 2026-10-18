"""ASTRA investigation engine (Person 2): one deterministic function from canonical records to an
explainable analysis.

    events (Person 1 NormalizedTransfer)  +  collected OSINT documents  +  config  [+ cutoff]
        -> graph index (adjacency lists / hash maps)
        -> time-respecting reachability (DFS) + min-hop paths (layered BFS)
        -> flow decomposition (FIFO traced value, residuals)
        -> OSINT indicator extraction + validation + correlation (Union-Find independence groups)
        -> per-candidate feature pipeline -> transparent priority score (astra-priority-v1)
        -> heap Top-K lead queue (+ Dijkstra fastest observed route for the top leads)
        -> evidence items + claim graph (bidirectional) -> H1/H2/H3 hypotheses + falsification
        -> timeline, unresolved boundaries, provenance and limitations

Sensitivity analysis and historical replay call this SAME function with evidence overrides or a
cutoff; nothing is precomputed or looked up by scenario id. The score is a prioritisation aid, not a
probability, and nothing here asserts identity, ownership or criminality.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from . import dsa
from .correlation import correlate
from .evidence import EvidenceItem, Hypothesis, score_hypothesis
from .osint import extract_indicators
from .risk_scoring import PENALTIES, WEIGHTS, score_lead

ENGINE_VERSION = "astra-intel-engine-1.0.0"
METHOD_VERSIONS = {
    "engine": ENGINE_VERSION, "priority_score": "astra-priority-v1 (+not_applicable, weight overrides)",
    "hypotheses": "astra-hypotheses-v1 (P2 heuristic score_hypothesis + H3 decisiveness rule)",
    "osint_extraction": "regex_indicator_extraction_v2+sentence_stance_v1",
    "flow_decomposition": "fifo-traced-value-v1", "shortest_paths": "bfs-time-respecting-v1 / dijkstra-fastest-v1",
}
H3_SEPARATION_SCALE = 0.15   # |H1-H2| at which the evidence is considered fully discriminating
H3_MASS_SCALE = 0.35         # evidence mass (1-exp(-0.35*sum r*s)) at which coverage is considered adequate
CALCULATED_RELIABILITY = 0.7  # reliability attached to calculated timing/shape features (weak signals)
SLOW_FORWARD_SECONDS = 6 * 3600

STATUS = ("Observed", "Calculated", "Source-reported", "Inferred", "Disputed", "Unresolved")


# --------------------------------------------------------------------------------------------
# configuration / overrides
# --------------------------------------------------------------------------------------------
@dataclass
class AnalysisConfig:
    max_hops: int = 6
    max_candidates: int = 300
    top_k: int = 10
    rapid_window_seconds: float = 600.0
    freshness_half_life_days: float = 90.0
    stale_after_days: float = 180.0
    hop_penalty_seconds: float = 3600.0
    replay_policy: str = "observation"      # observation | publication
    weights: dict[str, float] = field(default_factory=dict)
    penalties: dict[str, float] = field(default_factory=dict)
    max_expansions: int = 500_000

    def validate(self) -> "AnalysisConfig":
        if not 1 <= self.max_hops <= 12:
            raise ValueError("max_hops must be 1..12")
        if not 1 <= self.top_k <= 100:
            raise ValueError("top_k must be 1..100")
        if not 1 <= self.max_candidates <= 5000:
            raise ValueError("max_candidates must be 1..5000")
        for name in ("rapid_window_seconds", "freshness_half_life_days", "stale_after_days"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                raise ValueError(f"{name} must be a positive number")
        if self.hop_penalty_seconds < 0:
            raise ValueError("hop_penalty_seconds must be >= 0")
        if self.replay_policy not in ("observation", "publication"):
            raise ValueError("replay_policy must be 'observation' or 'publication'")
        return self

    def as_dict(self) -> dict:
        return asdict(self)

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class Overrides:
    remove_evidence_ids: list[str] = field(default_factory=list)
    downgrade_evidence_ids: list[str] = field(default_factory=list)
    downgrade_factor: float = 0.5
    remove_doc_ids: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.remove_evidence_ids or self.downgrade_evidence_ids or self.remove_doc_ids)


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def _epoch(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return int(v.timestamp())
    if isinstance(v, (int, float)):
        return int(v)
    return _epoch(datetime.fromisoformat(str(v).replace("Z", "+00:00")))


def _iso(ts: Optional[int]) -> Optional[str]:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def format_units(raw: int, decimals: Optional[int]) -> Optional[str]:
    if decimals is None:
        return None
    if decimals == 0:
        return str(raw)
    whole, frac = divmod(raw, 10 ** decimals)
    s = f"{whole}.{frac:0{decimals}d}".rstrip("0")
    return s[:-1] if s.endswith(".") else s


def _addr(n: str) -> str:
    return n.split(":", 1)[1]


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def edge_from_transfer(t: Any) -> dsa.Edge:
    """Duck-typed adapter from Person 1's NormalizedTransfer (or an equivalent dict)."""
    g = (lambda k: t[k]) if isinstance(t, dict) else (lambda k: getattr(t, k))
    return dsa.Edge(event_id=g("event_id"), u=dsa.node_id(g("chain_id"), g("from_address")),
                    v=dsa.node_id(g("chain_id"), g("to_address")), ts=_epoch(g("timestamp")),
                    asset_id=g("asset_id"), raw=int(g("raw_value")), tx_hash=g("transaction_hash"),
                    decimals=g("token_decimals"), symbol=g("asset_symbol"))


def doc_availability(doc: dict, policy: str) -> Optional[int]:
    """When a document counts as 'known' for replay.

    observation (default, strict): the time ASTRA (or the analyst) actually retrieved it.
    publication: the source's publication time when known, else its retrieval time.
    """
    pub, ret = _epoch(doc.get("published_at")), _epoch(doc.get("retrieved_at"))
    if policy == "publication":
        return pub if pub is not None else ret
    return ret if ret is not None else pub


# --------------------------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------------------------
def analyze(*, investigation_id: str, chain_id: int, start_address: Optional[str], events: Iterable[Any],
            documents: Iterable[dict], data_mode: str, dataset_ref: str, config: Optional[AnalysisConfig] = None,
            cutoff: Optional[datetime] = None, overrides: Optional[Overrides] = None,
            source_status: Optional[list[dict]] = None, duplicates_dropped: int = 0,
            extra_limitations: Iterable[str] = ()) -> dict[str, Any]:
    cfg = (config or AnalysisConfig()).validate()
    ov = overrides or Overrides()
    if not 0.0 <= ov.downgrade_factor <= 1.0:
        raise ValueError("downgrade_factor must be in [0, 1]")
    cutoff_ts = _epoch(cutoff) if cutoff is not None else None

    # ---- 1. records available at the cutoff (event time for transfers; policy time for documents)
    all_events = [e for e in events if (e["status"] if isinstance(e, dict) else e.status) == "success"]
    edges_all = [edge_from_transfer(e) for e in all_events]
    edges = [e for e in edges_all if cutoff_ts is None or e.ts <= cutoff_ts]
    docs_all = sorted(documents, key=lambda d: d["doc_id"])
    removed_docs = set(ov.remove_doc_ids)
    docs, docs_future, docs_unusable = [], [], []
    for d in docs_all:
        if d["doc_id"] in removed_docs:
            continue
        if d.get("collection_status", "collected") != "collected" or not d.get("text"):
            docs_unusable.append(d)
            continue
        av = doc_availability(d, cfg.replay_policy)
        if cutoff_ts is not None and (av is None or av > cutoff_ts):
            docs_future.append(d)
            continue
        docs.append(d)

    g = dsa.GraphIndex(edges)
    start = dsa.node_id(chain_id, start_address) if start_address else None
    start_in_graph = start is not None and start in g.nodes
    first_out = [e.ts for e in g.out_adj.get(start, [])] if start_in_graph else []
    incident_start = min(first_out) if first_out else (min((e.ts for e in edges), default=None))
    as_of = cutoff_ts if cutoff_ts is not None else max(
        [e.ts for e in edges] + [x for x in (doc_availability(d, cfg.replay_policy) for d in docs) if x is not None],
        default=0)

    # ---- 2. DSA: reachability, paths, flow, components
    reach = dsa.time_respecting_reachability(g, start, start_time=incident_start, max_expansions=cfg.max_expansions) \
        if start_in_graph else dsa.Reachability({}, {}, 0, False)
    min_hops = dsa.min_hop_time_respecting(g, start, start_time=incident_start, max_hops=cfg.max_hops) \
        if start_in_graph else {}
    uf = dsa.weak_components(g)
    comps = uf.groups()
    incident_comp = uf.find(start) if start_in_graph else None
    flows: dict[str, dsa.FlowDecomposition] = {}
    if start_in_graph:
        for asset in sorted({e.asset_id for e in g.out_adj.get(start, [])}):
            flows[asset] = dsa.decompose_flow(g, start, asset, start_time=incident_start)
    degree_pct = dsa.percentile_ranks({n: float(g.degree(n)) for n in g.nodes})

    # ---- 3. OSINT: extraction, validation, correlation
    indicators: list[dict] = []
    for d in docs:
        indicators.extend(extract_indicators(d))
    corr = correlate(indicators, {d["doc_id"]: d for d in docs}, g, chain_id, as_of=as_of,
                     incident_start=incident_start, half_life_days=cfg.freshness_half_life_days,
                     stale_after_days=cfg.stale_after_days)
    findings = corr["findings"]
    fmap = {f["finding_id"]: f for f in findings}
    # sensitivity on OSINT evidence acts on the underlying finding (other findings stay intact)
    removed_ev = set(ov.remove_evidence_ids)
    down_ev = set(ov.downgrade_evidence_ids)
    removed_fids = {"F-" + x[len("EV-OSINT-"):] for x in removed_ev if x.startswith("EV-OSINT-")}
    for x in down_ev:
        fid = "F-" + x[len("EV-OSINT-"):] if x.startswith("EV-OSINT-") else None
        if fid in fmap:
            fmap[fid] = dict(fmap[fid], source_reliability=round((fmap[fid]["source_reliability"] or 0) * ov.downgrade_factor, 4))
    by_node = {n: [f for f in fids if f not in removed_fids] for n, fids in corr["by_node"].items()}

    # ---- 4. candidate set (bounded, deterministic)
    reachable = sorted((n for n in reach.earliest_arrival if n != start),
                       key=lambda n: (min_hops[n].hops if n in min_hops else 99, n))
    osint_nodes = sorted(n for n, fids in by_node.items() if n != start and fids)
    cand_order = list(dict.fromkeys(reachable + osint_nodes))
    candidates_truncated = len(cand_order) > cfg.max_candidates
    candidates = cand_order[:cfg.max_candidates]

    # ---- 5. case-level (origin) evidence shared by every lead's hypotheses
    case_evidence: list[dict] = []
    if start_in_graph:
        case_evidence += _osint_evidence(start, by_node.get(start, []), fmap, case_level=True)
        case_evidence += _origin_shape_evidence(g, start, incident_start, cfg)

    # ---- 6. per-candidate features, evidence, scoring
    case_evidence = _apply_overrides(case_evidence, removed_ev, down_ev, ov.downgrade_factor)
    leads: list[dict] = []
    for n in candidates:
        feats = _features(g, n, start, min_hops, flows, degree_pct, reach, cfg)
        feats["in_incident_component"] = (uf.find(n) == incident_comp) if incident_comp is not None else None
        ev = _candidate_evidence(g, n, feats, min_hops, flows, cfg)
        ev += _osint_evidence(n, by_node.get(n, []), fmap)
        osig = _osint_signals(n, by_node.get(n, []), fmap)
        signals = _signals(feats, osig)
        ev = _apply_overrides(ev, removed_ev, down_ev, ov.downgrade_factor)
        signals, overridden = _signals_after_overrides(signals, ev)
        scored = score_lead(_addr(n), signals, weights=cfg.weights, penalties=cfg.penalties,
                            not_applicable=["model_illicit_score"])
        leads.append({"node_id": n, "address": _addr(n), "features": feats, "signals": signals,
                      "signal_overrides": overridden, "score": scored, "evidence": ev, "osint": osig})

    # ---- 7. ranking: heap Top-K with deterministic ties (score desc, hops asc, node id asc)
    key_hops = lambda L: L["features"]["hops"] if L["features"]["hops"] is not None else 99
    ranked = dsa.top_k(leads, len(leads), score=lambda L: L["score"]["priority_score"],
                       tiebreak=lambda L: (key_hops(L), L["node_id"]))
    lead_out: list[dict] = []
    all_evidence: dict[str, dict] = {e["evidence_id"]: e for e in case_evidence}
    claims: list[dict] = []
    for rank, L in enumerate(ranked, 1):
        lead = _assemble_lead(rank, L, case_evidence, start, g, cfg, incident_start, with_route=rank <= cfg.top_k)
        lead_out.append(lead)
        for e in L["evidence"]:
            all_evidence[e["evidence_id"]] = e
        claims.extend(lead.pop("_claims"))
    # bidirectional claim <-> evidence links
    for c in claims:
        for rel in ("support_evidence_ids", "contradiction_evidence_ids", "context_evidence_ids"):
            for eid in c[rel]:
                if eid in all_evidence:
                    all_evidence[eid].setdefault("claim_ids", [])
                    if c["claim_id"] not in all_evidence[eid]["claim_ids"]:
                        all_evidence[eid]["claim_ids"].append(c["claim_id"])
    for e in all_evidence.values():
        e.setdefault("claim_ids", [])
        e["claim_ids"].sort()

    case_hyp = lead_out[0]["hypotheses"] if lead_out else _hypotheses(case_evidence, [], None)
    case_fals = lead_out[0]["falsification"] if lead_out else _falsification(case_evidence, case_hyp)

    # ---- 8. timeline, boundaries, stats, metadata
    timeline = _timeline(edges_all, docs_all, cfg.replay_policy, cutoff_ts, removed_docs)
    boundaries = _boundaries(flows, findings, docs_unusable, start_in_graph, start, candidates_truncated,
                             reach, g)
    synthetic = data_mode == "synthetic" or any(d.get("data_mode") == "synthetic" for d in docs)
    limitations = [
        "Priority scores are transparent prioritisation aids (0-100), not probabilities, and are not calibrated.",
        "Graph connectivity, timing similarity and value allocation are observed relationships, not proof of "
        "common ownership, identity, or that the same funds moved.",
        "OSINT matches are source-reported claims; a mention does not establish control or wrongdoing.",
        "Flow decomposition uses a FIFO allocation assumption; other policies allocate value differently.",
        "No trained model is loaded: model_illicit_score is not applicable and excluded from the score.",
        *extra_limitations,
    ]
    if synthetic:
        limitations.insert(0, "SYNTHETIC / SIMULATED data: results demonstrate the pipeline, not real-world findings.")
    if candidates_truncated:
        limitations.append(f"Candidate set truncated to max_candidates={cfg.max_candidates}.")
    if reach.truncated:
        limitations.append("Reachability search hit its expansion budget; results may be incomplete.")

    doc_ids_hash = hashlib.sha256("|".join(d["doc_id"] + d.get("content_hash", "") for d in docs).encode()).hexdigest()[:12]
    snapshot = hashlib.sha256(json.dumps([investigation_id, dataset_ref, cutoff_ts, cfg.hash(), doc_ids_hash,
                                          len(edges), asdict(ov)], sort_keys=True).encode()).hexdigest()[:16]
    hyp_by_lead = {L["lead_id"]: L["hypotheses"]["leading"] for L in lead_out}
    return {
        "meta": {
            "investigation_id": investigation_id, "engine_version": ENGINE_VERSION, "method_versions": METHOD_VERSIONS,
            "data_mode": data_mode, "synthetic": synthetic,
            "data_label": "SYNTHETIC / SIMULATED" if synthetic else ("CACHED" if data_mode == "cached" else "LIVE"),
            "dataset_ref": dataset_ref, "config": cfg.as_dict(), "config_hash": cfg.hash(),
            "cutoff": _iso(cutoff_ts), "as_of": _iso(as_of), "replay_policy": cfg.replay_policy,
            "snapshot_id": snapshot, "overrides": asdict(ov), "incident_start": _iso(incident_start),
            "start_node": start, "start_in_graph": start_in_graph,
        },
        "graph_stats": {
            "nodes": len(g.nodes), "edges": g.edge_count, "weak_components": len(comps),
            "incident_component_size": uf.size[uf.find(start)] if start_in_graph else 0,
            "has_directed_cycle": dsa.has_directed_cycle(g), "self_loops": g.self_loops,
            "repeated_edge_pairs": g.repeated_edge_pairs(), "duplicates_dropped_at_ingestion": duplicates_dropped,
            "time_respecting_reachable": max(0, len(reach.earliest_arrival) - 1), "assets": g.assets(),
            "events_excluded_by_cutoff": len(edges_all) - len(edges),
        },
        "flow": [_flow_out(f) for f in flows.values()],
        "leads": lead_out,
        "top_k": [L["lead_id"] for L in lead_out[:cfg.top_k]],
        "evidence": sorted(all_evidence.values(), key=lambda e: e["evidence_id"]),
        "claims": sorted(claims, key=lambda c: c["claim_id"]),
        "case_hypotheses": case_hyp, "case_falsification": case_fals,
        "hypothesis_by_lead": hyp_by_lead,
        "osint": {
            "documents": [_doc_public(d) for d in docs],
            "documents_excluded_by_cutoff": [{"doc_id": d["doc_id"], "available_at": _iso(doc_availability(d, cfg.replay_policy))}
                                             for d in docs_future],
            "documents_unusable": [{"doc_id": d["doc_id"], "status": d.get("collection_status"), "error": d.get("error")}
                                   for d in docs_unusable],
            "indicators": indicators, "findings": findings, "independence_groups": corr["groups"],
            "match_summary": _count(f["match_type"] for f in findings),
            "sources": source_status or [],
        },
        "timeline": timeline,
        "boundaries": boundaries,
        "limitations": limitations,
    }


# --------------------------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------------------------
def _features(g, n, start, min_hops, flows, degree_pct, reach, cfg) -> dict:
    hops = min_hops[n].hops if n in min_hops else None
    reachable_tr = n in reach.earliest_arrival
    static = None
    if not reachable_tr and start is not None and start in g.nodes:
        sp = dsa.bfs_min_hops_static(g, start, n, max_hops=cfg.max_hops)
        static = sp.hops if sp.found else None
    ins, outs = g.in_adj.get(n, []), g.out_adj.get(n, [])
    in_ts = [e.ts for e in ins]
    deltas = []
    for e in outs:
        i = bisect.bisect_right(in_ts, e.ts)
        if i:
            deltas.append(e.ts - in_ts[i - 1])
    rapid = (sum(1 for d in deltas if d <= cfg.rapid_window_seconds) / len(deltas)) if deltas else (0.0 if ins and not outs else None)
    out_targets = {e.v for e in outs if e.v != n}
    in_sources = {e.u for e in ins if e.u != n}
    traced = {}
    for a, f in flows.items():
        tin = f.node_traced_in_raw.get(n, 0)
        tot = sum(e.raw for e in ins if e.asset_id == a and not e.is_self_loop)
        if tin or tot:
            dec = next((e.decimals for e in ins if e.asset_id == a), None)
            sym = next((e.symbol for e in ins if e.asset_id == a), None)
            traced[a] = {"traced_in_raw": str(tin), "total_in_raw": str(tot), "symbol": sym,
                         "traced_in": format_units(tin, dec), "total_in": format_units(tot, dec),
                         "continuity": round(tin / tot, 4) if tot else None,
                         "share_of_initial": round(tin / f.initial_raw, 4) if f.initial_raw else None,
                         "residual_raw": str(f.residual_by_node.get(n, 0)),
                         "residual": format_units(f.residual_by_node.get(n, 0), dec)}
    cont = [v["continuity"] for v in traced.values() if v["continuity"] is not None and int(v["traced_in_raw"]) > 0]
    return {
        "hops": hops, "time_respecting_reachable": reachable_tr, "static_hops_if_not_time_respecting": static,
        "earliest_arrival": _iso(reach.earliest_arrival.get(n)) if reachable_tr else None,
        "in_transfers": len(ins), "out_transfers": len(outs), "distinct_in_sources": len(in_sources),
        "distinct_out_targets": len(out_targets), "degree_percentile": round(degree_pct.get(n, 0.0), 4),
        "receipt_to_forward_deltas_s": deltas[:25], "median_receipt_to_forward_s": dsa.median(deltas),
        "rapid_forwarding_ratio": None if rapid is None else round(rapid, 4),
        "fragmentation": round(min(1.0, max(0, len(out_targets) - 1) / 4), 4),
        "consolidation": round(min(1.0, max(0, len(in_sources) - 1) / 4), 4),
        "flow_continuity": max(cont) if cont else (None if not reachable_tr else 0.0),
        "traced_by_asset": traced,
        "first_seen": _iso(min([e.ts for e in ins + outs], default=None)),
        "last_seen": _iso(max([e.ts for e in ins + outs], default=None)),
        "in_incident_component": None,
    }


def _osint_signals(n, fids, fmap) -> dict:
    sup = [fmap[f] for f in fids if fmap[f]["stance"] in ("incident_association", "neutral")
           and not fmap[f]["predates_incident"] and fmap[f]["match_quality"] > 0]
    con = [fmap[f] for f in fids if fmap[f]["stance"] in ("service_label", "mixed") and fmap[f]["match_quality"] > 0]
    groups = sorted({f["independence_group"] for f in sup})
    fresh = [f["freshness"]["score"] for f in sup if f["freshness"]["score"] is not None]
    return {
        "supporting_finding_ids": sorted(f["finding_id"] for f in sup),
        "contradicting_finding_ids": sorted(f["finding_id"] for f in con),
        "context_finding_ids": sorted(set(fids) - {f["finding_id"] for f in sup + con}),
        "independent_groups": groups,
        "indicator_match": max((f["match_quality"] for f in sup), default=None),
        "source_quality": max((f["source_reliability"] or 0.0 for f in sup), default=None),
        "independent_corrob": (round(min(1.0, (len(groups) - 1) / 2), 4) if groups else None),
        "source_freshness": max(fresh) if fresh else None,
        "contradiction_strength": (round(max((f["source_reliability"] or 0) * f["match_quality"] *
                                             (f["freshness"]["score"] if f["freshness"]["score"] is not None else 0.6)
                                             for f in con), 4) if con else (0.0 if fids else None)),
        "has_findings": bool(fids),
    }


def _signals(f: dict, o: dict) -> dict:
    """Map features to astra-priority-v1 signal names. ``None`` = missing (listed, never negative)."""
    hops = f["hops"]
    missing_link = 0.0 if f["time_respecting_reachable"] else (0.5 if f["static_hops_if_not_time_respecting"] else 1.0)
    return {
        "temporal": {"rapid_forwarding": f["rapid_forwarding_ratio"], "fragmentation": f["fragmentation"],
                     "consolidation": f["consolidation"]},
        "graph": {"incident_path_proximity": None if hops is None else round(1.0 / hops, 4),
                  "unusual_degree": f["degree_percentile"], "flow_continuity": f["flow_continuity"]},
        "osint": {k: o[k] for k in ("source_quality", "independent_corrob", "indicator_match", "source_freshness")},
        "evidence": {"contradiction_strength": o["contradiction_strength"], "missing_link_strength": missing_link},
    }


def _strip_none(sig: dict) -> dict:
    return {k: {kk: vv for kk, vv in v.items() if vv is not None} for k, v in sig.items()}


def _signals_after_overrides(signals: dict, evidence: list[dict]) -> tuple[dict, list[dict]]:
    """Removed evidence makes the signals it feeds MISSING; downgraded evidence scales them."""
    sig = json.loads(json.dumps(signals))
    changes = []
    for e in evidence:
        if e.get("override") not in ("removed", "downgraded"):
            continue
        for path in e.get("feeds_signals", []):
            grp, key = path.split(".")
            if sig.get(grp, {}).get(key) is None:
                continue
            before = sig[grp][key]
            if e["override"] == "removed":
                sig[grp][key] = None
            else:
                sig[grp][key] = round(before * e["override_factor"], 4)
            changes.append({"signal": path, "before": before, "after": sig[grp][key], "evidence_id": e["evidence_id"],
                            "override": e["override"]})
    return _strip_none(sig), changes


# --------------------------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------------------------
def _ev(eid, entity, kind, status, title, interpretation, reliability, rel_h1, rel_h2, s_h1, s_h2, refs, feeds,
        source, verified=False, provenance=None) -> dict:
    return {"evidence_id": eid, "entity": entity, "kind": kind, "status": status, "title": title,
            "interpretation": interpretation, "reliability": round(reliability, 4),
            "hypothesis_relations": {"H1": {"direction": rel_h1, "strength": round(s_h1, 4)},
                                     "H2": {"direction": rel_h2, "strength": round(s_h2, 4)}},
            "refs": refs, "feeds_signals": feeds, "source": source, "verified": verified,
            "provenance": provenance or {}, "override": None, "override_factor": None}


def _candidate_evidence(g, n, f, min_hops, flows, cfg) -> list[dict]:
    a = _addr(n)
    out = []
    if n in min_hops:
        p = min_hops[n]
        out.append(_ev(f"EV-PATH-{a}", n, "on_chain_path", "Observed",
                       f"Time-ordered path from incident origin ({p.hops} hop{'s' if p.hops != 1 else ''})",
                       "Observed transfers form a time-respecting path from the origin. Both an incident flow and "
                       "ordinary service payouts produce such paths, so it defines the lead but does not discriminate "
                       "between hypotheses.", 1.0, "context", "context", 0.0, 0.0,
                       {"event_ids": p.event_ids, "node_ids": p.node_ids}, ["graph.incident_path_proximity"],
                       "on-chain transfer records", verified=True))
    tr = {k: v for k, v in f["traced_by_asset"].items() if int(v["traced_in_raw"]) > 0}
    if tr:
        best = max(tr.items(), key=lambda kv: (kv[1]["share_of_initial"] or 0, kv[0]))
        eids = sorted(eid for fl in flows.values() for eid, amt in fl.edge_traced_raw.items()
                      if amt and g.by_event[eid].v == n)
        out.append(_ev(f"EV-FLOW-{a}", n, "flow_decomposition", "Calculated",
                       f"Traced value received: {best[1]['traced_in'] or best[1]['traced_in_raw']} "
                       f"{best[1]['symbol'] or ''} ({(best[1]['share_of_initial'] or 0) * 100:.1f}% of origin outflow)".strip(),
                       "FIFO flow decomposition allocates this portion of the origin's observed outflow to the address. "
                       "It is an allocation under a stated assumption, not proof that the same funds moved.",
                       CALCULATED_RELIABILITY, "context", "context", 0.0, 0.0,
                       {"event_ids": eids[:50], "asset_id": best[0]}, ["graph.flow_continuity"], "ASTRA flow decomposition"))
    r = f["rapid_forwarding_ratio"]
    if r is not None:
        med = f["median_receipt_to_forward_s"]
        slow = med is not None and med >= SLOW_FORWARD_SECONDS and f["out_transfers"] > 0
        out.append(_ev(f"EV-TEMP-{a}", n, "temporal_feature", "Calculated",
                       "Forwarding latency" + (f": median {med:.0f}s from receipt to forward" if med is not None else ": no forwarding observed"),
                       f"{r * 100:.0f}% of outgoing transfers left within {cfg.rapid_window_seconds:.0f}s of the preceding "
                       "receipt. Rapid forwarding is consistent with fund dispersal but also occurs in automated services.",
                       CALCULATED_RELIABILITY, "supports" if r > 0 else "context", "supports" if slow else "context",
                       0.6 * r, 0.3 if slow else 0.0, {"deltas_s": f["receipt_to_forward_deltas_s"]},
                       ["temporal.rapid_forwarding"], "ASTRA temporal features"))
    hub = f["consolidation"] >= 0.75 and f["degree_percentile"] >= 0.8
    split = f["fragmentation"] >= 0.5
    if f["in_transfers"] + f["out_transfers"]:
        out.append(_ev(f"EV-SHAPE-{a}", n, "graph_shape", "Calculated",
                       f"Fan-out {f['distinct_out_targets']} / fan-in {f['distinct_in_sources']} counterparties "
                       f"(degree percentile {f['degree_percentile']:.2f})",
                       ("Hub-like fan-in is typical of exchanges and other services. " if hub else "")
                       + ("Splitting to several recipients resembles dispersal (peel/split) but is also common in payouts."
                          if split else "Shape features are descriptive context."),
                       CALCULATED_RELIABILITY, "supports" if split and not hub else "context",
                       "supports" if hub else "context", 0.3 * f["fragmentation"] if split and not hub else 0.0,
                       0.5 * f["consolidation"] if hub else 0.0,
                       {"distinct_out_targets": f["distinct_out_targets"], "distinct_in_sources": f["distinct_in_sources"]},
                       ["temporal.fragmentation", "temporal.consolidation", "graph.unusual_degree"], "ASTRA graph metrics"))
    res = [(k, v) for k, v in tr.items() if int(v["residual_raw"]) > 0]
    if res:
        k, v = res[0]
        out.append(_ev(f"EV-GAP-{a}", n, "unresolved_residual", "Unresolved",
                       f"Traced value not observed leaving: {v['residual'] or v['residual_raw']} {v['symbol'] or ''}".strip(),
                       "Within the observed window this traced value stays at the address. Later transfers, internal "
                       "transfers or off-chain movement are unknown.", 1.0, "context", "context", 0.0, 0.0,
                       {"asset_id": k}, [], "ASTRA flow decomposition"))
    if not f["time_respecting_reachable"]:
        out.append(_ev(f"EV-NOLINK-{a}", n, "missing_link", "Unresolved",
                       "No observed time-ordered path from the incident origin",
                       "This address is linked only through public-source evidence"
                       + (" (a path exists only if time order is ignored)." if f["static_hops_if_not_time_respecting"] else "."),
                       1.0, "context", "context", 0.0, 0.0, {}, ["evidence.missing_link_strength"], "ASTRA graph engine"))
    return out


def _osint_evidence(n, fids, fmap, case_level=False) -> list[dict]:
    out = []
    best_in_group: dict[tuple, str] = {}
    for fid in sorted(fids):
        f = fmap[fid]
        k = (f["independence_group"], f["stance"])
        cur = best_in_group.get(k)
        if cur is None or (f["match_quality"], fmap[cur]["source_reliability"] or 0) > (fmap[cur]["match_quality"], fmap[cur]["source_reliability"] or 0):
            best_in_group[k] = fid
    scoring = set(best_in_group.values())
    for fid in sorted(fids):
        f = fmap[fid]
        fr = f["freshness"]["score"]
        strength = max(0.05, f["match_quality"] * (fr if fr is not None else 0.6))
        rel = f["source_reliability"] or 0.0
        st = f["stance"]
        if f["predates_incident"] and st == "incident_association":
            h1, h2, status = "context", "context", "Source-reported"
        elif st == "incident_association":
            h1, h2, status = "supports", "contradicts", "Source-reported"
        elif st == "service_label":
            h1, h2, status = "contradicts", "supports", "Source-reported"
        elif st == "privacy_boundary":
            h1, h2, status = "context", "context", "Unresolved"
        else:
            h1, h2, status = "context", "context", "Source-reported"
        if f["contradictory"] or st == "mixed":
            status = "Disputed"
        dup = fid not in scoring
        if dup:  # same independence group & stance as a stronger finding: not independent corroboration
            h1, h2 = "context", "context"
        title = {"incident_association": "Source reports incident association",
                 "service_label": "Source labels address as a service",
                 "privacy_boundary": "Source reports a privacy/mixer boundary",
                 "mixed": "Source makes mixed claims", "neutral": "Source mentions address"}[st]
        feeds = (["evidence.contradiction_strength"] if st in ("service_label", "mixed") else
                 ["osint.indicator_match", "osint.source_quality", "osint.independent_corrob", "osint.source_freshness"])
        out.append(_ev(f"EV-OSINT-{fid[2:]}", n, "osint_indicator", status,
                       f"{title} ({f['match_type']} match) — {f['source_name']}",
                       f"{f['link_reason']} Stance terms: {', '.join(f['stance_terms']) or 'none'}. "
                       + ("Duplicate/same-publisher copy: not counted as independent corroboration. " if dup else "")
                       + ("Published before the incident (prior activity). " if f["predates_incident"] else "")
                       + ("Freshness unknown. " if fr is None else ("Stale source. " if f["freshness"]["stale"] else "")),
                       rel, h1, h2, strength if h1 != "context" else 0.0, strength if h2 != "context" else 0.0,
                       {"finding_id": fid, "doc_id": f["doc_id"], "indicator_id": f["indicator_id"],
                        "event_ids": f["matched_event_ids"][:20]}, [] if case_level else feeds, f["source_name"] or "OSINT",
                       provenance={"source_url": f["source_url"], "excerpt": f["excerpt"], "match_type": f["match_type"],
                                   "freshness": f["freshness"], "evidence_kind": f["evidence_kind"],
                                   "data_mode": f["data_mode"], "independence_group": f["independence_group"],
                                   "contradicting_finding_ids": f["contradicting_finding_ids"], "case_level": case_level}))
    return out


def _origin_shape_evidence(g, start, incident_start, cfg) -> list[dict]:
    outs = g.out_adj.get(start, [])
    if len(outs) < 2:
        return []
    span = outs[-1].ts - outs[0].ts
    targets = len({e.v for e in outs})
    gaps = [b.ts - a.ts for a, b in zip(outs, outs[1:])]
    med_gap = dsa.median(gaps) or 0
    rapid_burst = span <= 3600 and targets >= 2
    # many recipients paid at a steady pace over several hours resembles routine batched payouts
    long_payout = span >= 4 * 3600 and targets >= 5 and med_gap >= 300
    a = _addr(start)
    return [_ev(f"EV-ORIGIN-{a}", start, "origin_pattern", "Calculated",
                f"Origin dispersal: {len(outs)} outgoing transfers to {targets} addresses over {span / 3600:.1f} h",
                ("A burst of transfers within one hour is consistent with post-incident dispersal. " if rapid_burst else "")
                + ("Steady transfers to many recipients over several hours resemble routine payouts. " if long_payout else "")
                + "Timing alone does not establish intent.", CALCULATED_RELIABILITY,
                "supports" if rapid_burst else "context", "supports" if long_payout else "context",
                0.5 if rapid_burst else 0.0, 0.5 if long_payout else 0.0,
                {"event_ids": [e.event_id for e in outs[:30]]}, [], "ASTRA temporal features")]


def _apply_overrides(evidence, removed, down, factor) -> list[dict]:
    out = []
    for e in evidence:
        e = dict(e)
        if e["kind"] == "osint_indicator":
            # already applied at the finding level (reliability scaled / finding dropped)
            if e["evidence_id"] in down:
                e["override"], e["override_factor"] = "downgraded", factor
                e["feeds_signals"] = []
            out.append(e)
            continue
        if e["evidence_id"] in removed:
            e["override"], e["override_factor"] = "removed", 0.0
        elif e["evidence_id"] in down:
            e["override"], e["override_factor"] = "downgraded", factor
            e["reliability"] = round(e["reliability"] * factor, 4)
        out.append(e)
    return out


# --------------------------------------------------------------------------------------------
# hypotheses, falsification, claims
# --------------------------------------------------------------------------------------------
HYP_TITLES = {"H1": "Potential incident-related movement", "H2": "Benign service / exchange / operational behaviour",
              "H3": "Insufficient evidence to distinguish H1 and H2"}


def _hyp_items(evidence: list[dict], hyp: str) -> list[EvidenceItem]:
    items = []
    for e in evidence:
        if e.get("override") == "removed":
            continue
        rel = e["hypothesis_relations"][hyp]
        items.append(EvidenceItem(evidence_id=e["evidence_id"], kind=e["kind"], direction=rel["direction"],
                                  reliability=min(1.0, max(0.0, e["reliability"])),
                                  strength=min(1.0, max(0.0, rel["strength"])), source=e["source"],
                                  description=e["title"], verified=e["verified"]))
    return items


def _hypotheses(case_ev: list[dict], lead_ev: list[dict], lead_id: Optional[str]) -> dict:
    ev = case_ev + lead_ev
    h1 = score_hypothesis(Hypothesis(hypothesis_id="H1", title=HYP_TITLES["H1"], evidence=_hyp_items(ev, "H1")))
    h2 = score_hypothesis(Hypothesis(hypothesis_id="H2", title=HYP_TITLES["H2"], evidence=_hyp_items(ev, "H2")))
    diag = 0.0
    for e in ev:
        if e.get("override") == "removed":
            continue
        s = max((e["hypothesis_relations"][h]["strength"] for h in ("H1", "H2")
                 if e["hypothesis_relations"][h]["direction"] != "context"), default=0.0)
        diag += e["reliability"] * s
    mass = 1.0 - math.exp(-0.35 * diag)
    sep = abs(h1["heuristic_score"] - h2["heuristic_score"])
    decisiveness = min(1.0, sep / H3_SEPARATION_SCALE) * min(1.0, mass / H3_MASS_SCALE)
    s3 = round(max(0.0, min(1.0, 0.15 + 0.70 * (1.0 - decisiveness))), 4)
    scores = {"H1": h1["heuristic_score"], "H2": h2["heuristic_score"], "H3": s3}
    best = max(scores.values())
    leading = "H3" if scores["H3"] == best else ("H1" if scores["H1"] >= scores["H2"] else "H2")

    def lists(h):
        sup = [e["evidence_id"] for e in ev if e.get("override") != "removed" and e["hypothesis_relations"][h]["direction"] == "supports"]
        con = [e["evidence_id"] for e in ev if e.get("override") != "removed" and e["hypothesis_relations"][h]["direction"] == "contradicts"]
        return sorted(sup), sorted(con)
    s1, c1 = lists("H1")
    s2, c2 = lists("H2")
    missing = []
    if not any(e["kind"] == "osint_indicator" for e in ev):
        missing.append("No public-source evidence about the origin or this address.")
    if not any(e["kind"] == "osint_indicator" and e["hypothesis_relations"]["H2"]["direction"] == "supports" for e in ev):
        missing.append("No source-backed service/exchange label (would support H2).")
    if not any(e["kind"] == "osint_indicator" and e["hypothesis_relations"]["H1"]["direction"] == "supports" for e in ev):
        missing.append("No source-backed incident association (would support H1).")
    return {
        "lead_id": lead_id, "leading": leading, "score_type": "heuristic_review_score_not_probability",
        "hypotheses": [
            {"id": "H1", "title": HYP_TITLES["H1"], "score": scores["H1"], "supporting_evidence_ids": s1,
             "contradicting_evidence_ids": c1, "support_weight": h1["support_weight"],
             "contradiction_weight": h1["contradiction_weight"], "context_weight": h1["context_weight"]},
            {"id": "H2", "title": HYP_TITLES["H2"], "score": scores["H2"], "supporting_evidence_ids": s2,
             "contradicting_evidence_ids": c2, "support_weight": h2["support_weight"],
             "contradiction_weight": h2["contradiction_weight"], "context_weight": h2["context_weight"]},
            {"id": "H3", "title": HYP_TITLES["H3"], "score": s3, "supporting_evidence_ids": [], "contradicting_evidence_ids": [],
             "decisiveness": round(decisiveness, 4), "separation": round(sep, 4), "evidence_mass": round(mass, 4)},
        ],
        "assumptions": [
            "H1/H2 scores use Person 2's transparent heuristic (support/contradiction balance x saturating evidence mass).",
            f"H3 = 0.15 + 0.70 x (1 - decisiveness); decisiveness = min(1,|H1-H2|/{H3_SEPARATION_SCALE}) x "
            f"min(1, mass/{H3_MASS_SCALE}). Ties resolve to H3.",
            "On-chain paths and flow allocations are context for both H1 and H2 (both hypotheses predict them).",
        ],
        "missing_information": missing,
        "interpretation": "Comparative review scores, not probabilities. H3 is a legitimate outcome.",
    }


def _falsification(case_ev, hyp, lead_ev=()) -> dict:
    ev = list(case_ev) + list(lead_ev)
    lead_id = hyp.get("lead_id")
    leading = hyp["leading"]
    checks = []
    case_ids = {c["evidence_id"] for c in case_ev}
    if leading in ("H1", "H2"):
        sup = next(h for h in hyp["hypotheses"] if h["id"] == leading)["supporting_evidence_ids"]
        for eid in sup:
            reduced = [dict(e, override="removed") if e["evidence_id"] == eid else e for e in ev]
            after = _hypotheses([x for x in reduced if x["evidence_id"] in case_ids],
                                [x for x in reduced if x["evidence_id"] not in case_ids], lead_id)
            before_s = next(h["score"] for h in hyp["hypotheses"] if h["id"] == leading)
            after_s = next(h["score"] for h in after["hypotheses"] if h["id"] == leading)
            checks.append({"removed_evidence_id": eid, "leading_before": leading, "leading_after": after["leading"],
                           "score_before": before_s, "score_after": after_s, "score_delta": round(after_s - before_s, 4),
                           "conclusion_changes": after["leading"] != leading})
    weaken = {
        "H1": ["A source-backed service/exchange label for the origin or this address.",
               "Evidence that the transfers are routine (regular schedule, long-standing counterparties).",
               "Removal or downgrade of the strongest incident-association source (see checks)."],
        "H2": ["An independent, fresh source tying the origin or address to the incident.",
               "Rapid multi-hop forwarding inconsistent with the labelled service's normal behaviour."],
        "H3": ["Any additional independent public source about the origin or this address.",
               "Further observed transfers that extend or terminate the traced paths."],
    }[leading]
    alt = [h for h in hyp["hypotheses"] if h["id"] not in (leading, "H3")]
    return {"lead_id": lead_id, "leading": leading, "single_evidence_removal_checks": checks,
            "brittle": any(c["conclusion_changes"] for c in checks),
            "what_would_weaken_leading": weaken,
            "alternative_fit": [{"id": h["id"], "score": h["score"], "has_support": bool(h["supporting_evidence_ids"])} for h in alt],
            "note": "A diagnostic for dependence on individual evidence items, not a test of truth."}


def _next_check(L, osig, feats) -> str:
    a = _short(L["address"])
    if osig["contradicting_finding_ids"] and osig["supporting_finding_ids"]:
        return f"Resolve conflicting source claims about {a}: open both original sources and compare dates and context."
    if not osig["has_findings"]:
        return f"Search permitted public sources for {a}; no public-source evidence has been found yet."
    if osig["indicator_match"] is not None and osig["indicator_match"] < 1.0:
        return f"Confirm the full address in the source: the current match for {a} is not exact."
    if osig["source_freshness"] is not None and osig["source_freshness"] < 0.25:
        return f"Find a more recent source: current evidence for {a} is stale."
    if any(int(v["residual_raw"]) > 0 for v in feats["traced_by_asset"].values()):
        return f"Check for later transfers from {a}: traced value was not observed leaving within the window."
    return f"Verify the transfers of {a} on an independent block explorer and look for independent sources."


def _title(feats, osig, signals) -> str:
    if osig["contradicting_finding_ids"] and not osig["supporting_finding_ids"]:
        return "Service-labelled endpoint"
    if osig["contradicting_finding_ids"]:
        return "Conflicting public-source claims"
    if osig["supporting_finding_ids"]:
        return "Public indicator match"
    r = feats["rapid_forwarding_ratio"]
    if r is not None and r >= 0.5 and feats["out_transfers"]:
        return "Rapid forwarding relay"
    if feats["fragmentation"] >= 0.5:
        return "Fragmenting relay"
    if not feats["time_respecting_reachable"]:
        return "Source-only association"
    return "Traced value recipient"


def _assemble_lead(rank, L, case_ev, start, g, cfg, incident_start, with_route) -> dict:
    n, feats, sc, ev, osig = L["node_id"], L["features"], L["score"], L["evidence"], L["osint"]
    lead_id = f"LD-{L['address'][2:10]}{L['address'][-6:]}"
    hyp = _hypotheses(case_ev, ev, lead_id)
    fals = _falsification(case_ev, hyp, ev)
    reasons = []
    for c in sc["top_contributors"]:
        reasons.append(_reason(c, feats, osig))
    sup_ids = sorted({e["evidence_id"] for e in ev if e.get("override") != "removed"
                      and any(e["hypothesis_relations"][h]["direction"] == "supports" for h in ("H1",))})
    con_ids = sorted({e["evidence_id"] for e in ev if e.get("override") != "removed"
                      and e["hypothesis_relations"]["H1"]["direction"] == "contradicts"})
    ctx_ids = sorted({e["evidence_id"] for e in ev if e.get("override") != "removed"} - set(sup_ids) - set(con_ids))
    status = "disputed" if con_ids else ("unresolved" if hyp["leading"] == "H3" else "candidate")
    route = None
    if with_route and start in g.nodes and feats["time_respecting_reachable"]:
        r = dsa.dijkstra_fastest_route(g, start, n, start_time=incident_start,
                                       hop_penalty_seconds=cfg.hop_penalty_seconds, max_expansions=50_000)
        if r.found:
            route = {"event_ids": r.event_ids, "node_ids": r.node_ids, "hops": r.hops, "cost": r.cost,
                     "cost_breakdown": r.cost_breakdown,
                     "cost_model": f"sum(hop_penalty={cfg.hop_penalty_seconds:g}s + dwell seconds); amounts are not distances"}
    path_ev = next((e for e in ev if e["kind"] == "on_chain_path"), None)
    claims = _claims(lead_id, L, ev, case_ev, hyp, sup_ids, con_ids, ctx_ids, sc)
    return {
        "lead_id": lead_id, "rank": rank, "node_id": n, "address": L["address"], "title": _title(feats, osig, L["signals"]),
        "priority_score": sc["priority_score"], "priority_tier": sc["priority_tier"], "score_type": sc["score_type"],
        "components": sc["components"], "top_contributors": sc["top_contributors"], "missing_signals": sc["missing_signals"],
        "not_applicable_signals": sc["not_applicable_signals"], "signal_completeness": sc["signal_completeness"],
        "weights_used": sc["weights_used"], "signals": L["signals"], "signal_overrides": L["signal_overrides"],
        "features": feats, "reasons": reasons, "status": status,
        "support_evidence_ids": sup_ids, "contradiction_evidence_ids": con_ids, "context_evidence_ids": ctx_ids,
        "osint": {k: osig[k] for k in ("supporting_finding_ids", "contradicting_finding_ids", "context_finding_ids",
                                       "independent_groups")},
        "path": {"event_ids": path_ev["refs"]["event_ids"], "node_ids": path_ev["refs"]["node_ids"]} if path_ev else None,
        "fastest_route": route, "hypotheses": hyp, "falsification": fals, "next_check": _next_check(L, osig, feats),
        "interpretation": sc["interpretation"], "_claims": claims,
    }


def _reason(c, feats, osig) -> str:
    s, v = c["signal"], c["value"]
    if s == "graph.incident_path_proximity":
        return f"Time-ordered path from origin: {feats['hops']} hop(s)"
    if s == "graph.flow_continuity":
        return f"{v * 100:.0f}% of observed inflow is traced value (FIFO allocation)"
    if s == "graph.unusual_degree":
        return f"Degree percentile {v:.2f} within this graph"
    if s == "temporal.rapid_forwarding":
        med = feats["median_receipt_to_forward_s"]
        return f"Rapid forwarding ratio {v:.2f}" + (f" (median {med:.0f}s receipt→forward)" if med is not None else "")
    if s == "temporal.fragmentation":
        return f"Splits to {feats['distinct_out_targets']} recipients"
    if s == "temporal.consolidation":
        return f"Receives from {feats['distinct_in_sources']} sources"
    if s.startswith("osint."):
        return {"osint.indicator_match": f"Public indicator match quality {v:.2f}",
                "osint.source_quality": f"Source reliability rubric {v:.2f}",
                "osint.independent_corrob": f"{len(osint_groups(osig))} independent source group(s)",
                "osint.source_freshness": f"Source freshness {v:.2f}"}[s]
    if s == "evidence.contradiction_strength":
        return f"Contradicting public-source claim (penalty, strength {v:.2f})"
    if s == "evidence.missing_link_strength":
        return "No observed time-ordered path from origin (penalty)"
    return f"{s} = {v}"


def osint_groups(osig):
    return osig["independent_groups"]


def _claims(lead_id, L, ev, case_ev, hyp, sup_ids, con_ids, ctx_ids, sc) -> list[dict]:
    a = L["address"]
    out = []
    ids = {e["evidence_id"]: e for e in ev if e.get("override") != "removed"}
    path = [k for k, e in ids.items() if e["kind"] in ("on_chain_path", "flow_decomposition")]
    if path:
        out.append({"claim_id": f"CLM-{lead_id}-FLOW", "subject": a, "predicate": "received value traceable from",
                    "object": "incident origin", "claim_type": "observed_association", "status": "Observed",
                    "support_evidence_ids": sorted(path), "contradiction_evidence_ids": [],
                    "context_evidence_ids": sorted(k for k, e in ids.items() if e["kind"] == "unresolved_residual"),
                    "explanation": "Observed time-ordered transfers and a FIFO allocation link this address to the origin.",
                    "analysis_version": ENGINE_VERSION})
    osint_sup = [k for k, e in ids.items() if e["kind"] == "osint_indicator" and e["hypothesis_relations"]["H1"]["direction"] == "supports"]
    osint_con = [k for k, e in ids.items() if e["kind"] == "osint_indicator" and e["hypothesis_relations"]["H1"]["direction"] == "contradicts"]
    osint_ctx = [k for k, e in ids.items() if e["kind"] == "osint_indicator" and e["hypothesis_relations"]["H1"]["direction"] == "context"]
    if osint_sup or osint_con or osint_ctx:
        out.append({"claim_id": f"CLM-{lead_id}-OSINT", "subject": a, "predicate": "is publicly reported in connection with",
                    "object": "the incident", "claim_type": "source_reported_association",
                    "status": "Disputed" if (osint_sup and osint_con) else ("Source-reported" if osint_sup else "Unresolved"),
                    "support_evidence_ids": sorted(osint_sup), "contradiction_evidence_ids": sorted(osint_con),
                    "context_evidence_ids": sorted(osint_ctx),
                    "explanation": "Public sources mention this address; mentions are claims, not verified attribution.",
                    "analysis_version": ENGINE_VERSION})
    out.append({"claim_id": f"CLM-{lead_id}-PRIORITY", "subject": a, "predicate": "is a priority lead for review under",
                "object": hyp["leading"], "claim_type": "inferred_prioritisation",
                "status": "Disputed" if con_ids else ("Unresolved" if hyp["leading"] == "H3" else "Inferred"),
                "support_evidence_ids": sup_ids, "contradiction_evidence_ids": con_ids, "context_evidence_ids": ctx_ids,
                "score_components": [{"signal": c["signal"], "points": c["points"]} for c in sc["components"] if c["points"]],
                "explanation": f"Priority score {sc['priority_score']} ({sc['score_type']}); leading hypothesis {hyp['leading']}.",
                "analysis_version": ENGINE_VERSION})
    return out


# --------------------------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------------------------
def _flow_out(f: dsa.FlowDecomposition) -> dict:
    return {"asset_id": f.asset_id, "start": f.start, "initial_raw": str(f.initial_raw), "policy": f.policy,
            "conservation_ok": f.conservation_ok, "truncated": f.truncated,
            "path_flows": [{**p, "amount_raw": str(p["amount_raw"])} for p in f.path_flows[:200]],
            "path_flow_count": len(f.path_flows),
            "untraced_outflows": {k: str(v) for k, v in sorted(f.edge_untraced_raw.items())},
            "self_loops_skipped": f.self_loops_skipped,
            "assumptions": ["FIFO allocation of traced value at each address", "initial = origin's observed outflow",
                            "time-respecting; single asset; integer arithmetic"],
            "limitations": ["Allocation is not proof that the same funds moved.",
                            "Residuals are unresolved: value may have moved through unobserved channels."]}


def _timeline(edges_all, docs_all, policy, cutoff_ts, removed_docs) -> list[dict]:
    ev = [{"time": _iso(e.ts), "event_time": _iso(e.ts), "kind": "transfer", "ref": e.event_id,
           "title": f"Transfer {_short(_addr(e.u))} → {_short(_addr(e.v))}",
           "known_at_cutoff": cutoff_ts is None or e.ts <= cutoff_ts, "status": "Observed"} for e in edges_all]
    for d in docs_all:
        if d["doc_id"] in removed_docs:
            continue
        av = doc_availability(d, policy)
        ev.append({"time": _iso(av), "event_time": _iso(_epoch(d.get("published_at"))),
                   "retrieved_at": _iso(_epoch(d.get("retrieved_at"))), "kind": "source_document",
                   "ref": d["doc_id"], "title": d.get("title") or d.get("source_name"),
                   "known_at_cutoff": cutoff_ts is None or (av is not None and av <= cutoff_ts),
                   "status": "Source-reported" if d.get("collection_status", "collected") == "collected" else "Unresolved"})
    ev.sort(key=lambda x: (x["time"] or "9999", x["ref"]))
    return ev


def _boundaries(flows, findings, docs_unusable, start_in_graph, start, truncated, reach, g) -> list[dict]:
    out = []
    if not start_in_graph:
        out.append({"type": "origin_not_in_graph", "detail": "The case start address has no observed transfers in scope."})
    for f in flows.values():
        for n, amt in sorted(f.residual_by_node.items()):
            if amt and n != start:
                out.append({"type": "unresolved_residual", "node_id": n, "asset_id": f.asset_id, "amount_raw": str(amt),
                            "detail": "Traced value not observed leaving this address within the window."})
    for fd in findings:
        if fd["stance"] == "privacy_boundary" and fd["matched_nodes"]:
            out.append({"type": "privacy_boundary", "node_ids": fd["matched_nodes"], "finding_id": fd["finding_id"],
                        "detail": "Source reports a mixer/privacy boundary: entry/exit observed, no path asserted through it."})
        if fd["match_type"] == "ambiguous":
            out.append({"type": "ambiguous_indicator", "node_ids": fd["matched_nodes"], "finding_id": fd["finding_id"],
                        "detail": fd["link_reason"]})
    for d in docs_unusable:
        out.append({"type": "source_unavailable", "doc_id": d["doc_id"], "detail": d.get("error") or "not collected"})
    if truncated:
        out.append({"type": "candidate_limit", "detail": "Candidate set truncated."})
    if reach.truncated:
        out.append({"type": "expansion_budget", "detail": "Reachability truncated by expansion budget."})
    return out


def _doc_public(d: dict) -> dict:
    return {k: d.get(k) for k in ("doc_id", "source_id", "source_name", "source_type", "url", "title", "published_at",
                                  "retrieved_at", "content_hash", "extraction_method", "reliability",
                                  "reliability_rationale", "data_mode", "collection_status", "publisher_group",
                                  "terms_note")} | {"text_length": len(d.get("text") or "")}


def _count(xs) -> dict:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------------------------
# sensitivity: re-run the real engine and compare
# --------------------------------------------------------------------------------------------
def compare_runs(base: dict, alt: dict) -> dict:
    b = {L["lead_id"]: L for L in base["leads"]}
    a = {L["lead_id"]: L for L in alt["leads"]}
    rows = []
    for lid in sorted(set(b) | set(a), key=lambda x: (b.get(x, a.get(x))["rank"], x)):
        x, y = b.get(lid), a.get(lid)
        rows.append({"lead_id": lid, "address": (x or y)["address"],
                     "rank_before": x["rank"] if x else None, "rank_after": y["rank"] if y else None,
                     "score_before": x["priority_score"] if x else None, "score_after": y["priority_score"] if y else None,
                     "score_delta": round((y["priority_score"] if y else 0) - (x["priority_score"] if x else 0), 2),
                     "rank_change": (x["rank"] - y["rank"]) if x and y else None,
                     "leading_before": x["hypotheses"]["leading"] if x else None,
                     "leading_after": y["hypotheses"]["leading"] if y else None})
    removed = set(alt["meta"]["overrides"]["remove_evidence_ids"]) | set(alt["meta"]["overrides"]["downgrade_evidence_ids"])
    affected_claims = sorted({c["claim_id"] for c in base["claims"]
                              if removed & set(c["support_evidence_ids"] + c["contradiction_evidence_ids"] + c["context_evidence_ids"])})
    for d in alt["meta"]["overrides"]["remove_doc_ids"]:
        affected_claims += sorted({c["claim_id"] for c in base["claims"] for e in base["evidence"]
                                   if e.get("refs", {}).get("doc_id") == d and e["evidence_id"] in
                                   set(c["support_evidence_ids"] + c["contradiction_evidence_ids"] + c["context_evidence_ids"])})
    moved = [r for r in rows if r["rank_change"] not in (0, None) or r["score_delta"] != 0]
    return {"leads": rows, "moved_leads": moved, "affected_claim_ids": sorted(set(affected_claims)),
            "case_leading_before": base["case_hypotheses"]["leading"], "case_leading_after": alt["case_hypotheses"]["leading"],
            "top_lead_before": base["leads"][0]["lead_id"] if base["leads"] else None,
            "top_lead_after": alt["leads"][0]["lead_id"] if alt["leads"] else None}
