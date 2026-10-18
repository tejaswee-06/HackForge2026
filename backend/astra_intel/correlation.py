"""OSINT <-> on-chain correlation with explicit, inspectable rules (Person 2).

Match grades (``match_type``) and the numeric ``match_quality`` used by ranking:

  exact      1.00  full 40-hex address in the source equals a node in the investigation graph
                   (checksum valid or no checksum present)
  strong     0.75  full address equal after lower-casing BUT the source's EIP-55 checksum is invalid
                   (possible transcription error)
  strong     0.60  a transaction hash in the source equals an observed transaction; links to the
                   transfer's endpoints INDIRECTLY (the source may refer to either side)
  weak       0.40  a shortened form (0xabcd…1234) matches exactly one node in the graph
  ambiguous  0.00  a shortened form matches several nodes - attached to none of them for ranking
  no_match   0.00  indicator not found in this investigation (kept for the source register)

``contradictory`` is set when, for the same node, sources assert incompatible stances
(incident association vs. service label). Missing evidence is never converted into negative
evidence: a node with no findings simply has *missing* OSINT signals.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .dsa import GraphIndex, UnionFind, node_id

MATCH_QUALITY = {"exact": 1.0, "strong_checksum": 0.75, "strong_tx": 0.6, "weak": 0.4, "ambiguous": 0.0, "no_match": 0.0}


def _ts(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return int(v.astimezone(timezone.utc).timestamp())
    if isinstance(v, (int, float)):
        return int(v)
    return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp())


def independence_groups(docs: Iterable[dict]) -> UnionFind:
    """Union-Find over documents: identical content (syndicated copies, mirrors) or the same
    publisher are NOT independent corroboration. Symmetric relation -> DSU is appropriate."""
    uf = UnionFind()
    by_hash: dict[str, str] = {}
    by_pub: dict[str, str] = {}
    for d in sorted(docs, key=lambda d: d["doc_id"]):
        uf.add(d["doc_id"])
        h = d.get("content_hash")
        if h:
            uf.union(by_hash.setdefault(h, d["doc_id"]), d["doc_id"])
        pub = (d.get("publisher_group") or d.get("source_name") or "").strip().lower()
        if pub:
            uf.union(by_pub.setdefault(pub, d["doc_id"]), d["doc_id"])
    return uf


def freshness(published_ts: Optional[int], as_of: int, half_life_days: float, stale_after_days: float) -> dict:
    if published_ts is None:
        return {"status": "unknown", "age_days": None, "score": None, "stale": None,
                "note": "Publication time unknown; freshness cannot be assessed (treated as missing, not as stale)."}
    age = (as_of - published_ts) / 86400.0
    if age < 0:
        return {"status": "after_as_of", "age_days": round(age, 3), "score": None, "stale": None,
                "note": "Published after the analysis reference time."}
    return {"status": "stale" if age > stale_after_days else "fresh", "age_days": round(age, 3),
            "score": round(0.5 ** (age / half_life_days), 4), "stale": age > stale_after_days,
            "note": f"Exponential decay, half-life {half_life_days:g} days; stale after {stale_after_days:g} days."}


def correlate(indicators: list[dict], docs_by_id: dict[str, dict], g: GraphIndex, chain_id: int, *,
              as_of: int, incident_start: Optional[int], half_life_days: float = 90.0,
              stale_after_days: float = 180.0) -> dict[str, Any]:
    """Return {'findings': [...], 'by_node': {node: [finding_id]}, 'groups': {doc_id: group}}."""
    uf = independence_groups(docs_by_id.values())
    findings: list[dict] = []
    for ind in indicators:
        doc = docs_by_id[ind["doc_id"]]
        pub = _ts(doc.get("published_at"))
        fr = freshness(pub, as_of, half_life_days, stale_after_days)
        nodes: list[str] = []
        event_ids: list[str] = []
        unknowns = ["Control/ownership of the address is not established by a source mention."]
        t = ind["type"]
        if t == "evm_address":
            n = node_id(chain_id, ind["normalized_value"])
            if n in g.nodes:
                nodes = [n]
                event_ids = sorted({e.event_id for e in g.out_adj.get(n, [])} | {e.event_id for e in g.in_adj.get(n, [])})[:50]
                if ind["validation_status"] == "checksum_invalid":
                    mt, q, reason, kind = "strong", MATCH_QUALITY["strong_checksum"], (
                        "Full address matches after lower-casing, but the source's EIP-55 checksum is invalid "
                        "(possible transcription error)."), "direct"
                    unknowns.append("Checksum failure: the source may contain a typo of a different address.")
                else:
                    mt, q, reason, kind = "exact", MATCH_QUALITY["exact"], "Full address in the source equals a graph node.", "direct"
            else:
                mt, q, reason, kind = "no_match", 0.0, "Address not present in this investigation's graph.", "none"
        elif t == "tx_hash":
            edges = g.by_tx_hash.get(ind["normalized_value"], [])
            if edges:
                nodes = sorted({e.u for e in edges} | {e.v for e in edges})
                event_ids = sorted(e.event_id for e in edges)
                mt, q, reason, kind = "strong", MATCH_QUALITY["strong_tx"], (
                    "Transaction hash in the source equals an observed transaction; linked to its endpoints."), "indirect"
                unknowns.append("The source references a transaction, not a specific side of it.")
            else:
                mt, q, reason, kind = "no_match", 0.0, "Transaction hash not observed in this investigation.", "none"
        elif t == "truncated_evm_address":
            cands = sorted(n for n in g.nodes if n.split(":", 1)[1].startswith(ind["prefix"])
                           and n.endswith(ind["suffix"]))
            if len(cands) == 1:
                nodes = cands
                mt, q, reason, kind = "weak", MATCH_QUALITY["weak"], (
                    "Shortened address form matches exactly one node in this graph."), "indirect"
                unknowns.append("A shortened form may also match addresses outside this investigation.")
            elif len(cands) > 1:
                nodes = cands
                mt, q, reason, kind = "ambiguous", 0.0, (
                    f"Shortened address form matches {len(cands)} nodes; not attributed to any of them."), "indirect"
                unknowns.append("Ambiguous: cannot determine which address the source means.")
            else:
                mt, q, reason, kind = "no_match", 0.0, "Shortened form matches no node in this graph.", "none"
        else:
            mt, q, reason, kind = "no_match", 0.0, "Indicator type not supported for this chain.", "none"

        predates = pub is not None and incident_start is not None and pub < incident_start
        if predates and ind["stance"] == "incident_association":
            unknowns.append("Source predates the incident: it describes prior activity, not this incident.")
        if fr["status"] == "unknown":
            unknowns.append("Publication time unknown.")
        findings.append({
            "finding_id": f"F-{ind['indicator_id']}", "indicator_id": ind["indicator_id"], "doc_id": ind["doc_id"],
            "indicator_type": t, "matched_value": ind["normalized_value"], "validation_status": ind["validation_status"],
            "match_type": mt, "match_quality": q, "matched_nodes": nodes, "matched_event_ids": event_ids,
            "link_reason": reason, "evidence_kind": kind, "stance": ind["stance"], "stance_terms": ind["stance_terms"],
            "predates_incident": predates, "freshness": fr, "source_reliability": doc.get("reliability"),
            "source_name": doc.get("source_name"), "source_url": doc.get("url"), "excerpt": ind["excerpt"],
            "independence_group": uf.find(ind["doc_id"]), "data_mode": doc.get("data_mode"),
            "contradictory": False, "contradicting_finding_ids": [], "unknowns": unknowns,
            "features": {"matched_on": t, "validation_status": ind["validation_status"], "stance": ind["stance"],
                         "match_quality": q, "freshness_score": fr["score"], "source_reliability": doc.get("reliability")},
        })
    by_node: dict[str, list[str]] = {}
    for f in findings:
        if f["match_type"] in ("exact", "strong", "weak"):
            for n in f["matched_nodes"]:
                by_node.setdefault(n, []).append(f["finding_id"])
    fmap = {f["finding_id"]: f for f in findings}
    for n, fids in by_node.items():
        inc = [x for x in fids if fmap[x]["stance"] in ("incident_association", "mixed") and not fmap[x]["predates_incident"]]
        svc = [x for x in fids if fmap[x]["stance"] in ("service_label", "mixed")]
        if inc and svc and ({fmap[x]["doc_id"] for x in inc} != {fmap[x]["doc_id"] for x in svc} or
                            any(fmap[x]["stance"] == "mixed" for x in inc)):
            for x in inc:
                fmap[x]["contradictory"] = True
                fmap[x]["contradicting_finding_ids"] = sorted(set(fmap[x]["contradicting_finding_ids"]) | set(svc) - {x})
            for x in svc:
                fmap[x]["contradictory"] = True
                fmap[x]["contradicting_finding_ids"] = sorted(set(fmap[x]["contradicting_finding_ids"]) | set(inc) - {x})
    return {"findings": findings, "by_node": {k: sorted(v) for k, v in sorted(by_node.items())},
            "groups": {d: uf.find(d) for d in sorted(docs_by_id)}}
