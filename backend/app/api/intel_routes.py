"""Intelligence endpoints (Person 2 engine behind Person 1's API conventions).

Every response carries the shared metadata (investigation_id, data_mode, analysis_version,
generated_at, data_cutoff, limitations, sources) plus data_label / snapshot_id / config_hash, and
every number comes from ``astra_intel.engine.analyze`` for the requested inputs.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Body, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse

from astra_intel import dsa
from astra_intel.engine import AnalysisConfig, Overrides, compare_runs
from astra_intel.report import build_report, render_html

from ..errors import ApiError
from ..schemas.common import ErrorEnvelope, SourceRef
from ..schemas.intel import AnalystDocumentCreate, SensitivityRequest, SimulationCreate
from ..schemas.transaction import ADDRESS_RE, normalize_address
from ..services.analysis import to_config
from ..services.ingestion.normalizer import normalize_batch
from ..services.ingestion.service import IngestionResult
from ..services.osint_ingest.service import OSINTCollection, OSINTSourceError, analyst_document, build_document
from .routes import build_record, get_rec, meta, now, summary_response

router = APIRouter()
E = {404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}}


def _cfg(max_hops: int, top_k: int, rapid_window_seconds: float, replay_policy: str) -> AnalysisConfig:
    return AnalysisConfig(max_hops=max_hops, top_k=top_k, rapid_window_seconds=rapid_window_seconds,
                          replay_policy=replay_policy).validate()


def _aware(dt: Optional[datetime], name: str) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        raise ApiError(422, "VALIDATION_ERROR", f"{name} must be timezone-aware (e.g. 2026-10-01T02:30:00Z)")
    return dt


def _envelope(request, rec, a: dict, **body) -> dict:
    m = meta(rec)
    m["limitations"] = a["limitations"]
    if a["meta"]["cutoff"]:
        m["data_cutoff"] = a["meta"]["cutoff"]
    return {**m, "data_label": a["meta"]["data_label"], "synthetic": a["meta"]["synthetic"],
            "snapshot_id": a["meta"]["snapshot_id"], "config_hash": a["meta"]["config_hash"],
            "engine_version": a["meta"]["engine_version"], **body}


def _run(request: Request, inv_id: str, cfg: AnalysisConfig, cutoff=None, overrides=None):
    rec = get_rec(request, inv_id)
    try:
        return rec, request.app.state.analysis.run(rec, cfg, cutoff=cutoff, overrides=overrides)
    except ValueError as exc:
        raise ApiError(422, "INVALID_ANALYSIS_PARAMETERS", str(exc))


# Fresh Query objects per parameter: FastAPI must not share one FieldInfo between differently named params.
def Q_HOPS_():
    return Query(6, ge=1, le=12)


def Q_TOPK_():
    return Query(10, ge=1, le=100)


def Q_RAPID_():
    return Query(600.0, gt=0, le=30 * 86400)


def Q_POLICY_():
    return Query("observation", pattern="^(observation|publication)$")


@router.get("/api/investigations/{inv_id}/analysis", tags=["intelligence"], responses=E)
def get_analysis(inv_id: str, request: Request, cutoff: Optional[datetime] = None, max_hops: int = Q_HOPS_(),
                 top_k: int = Q_TOPK_(), rapid_window_seconds: float = Q_RAPID_(), replay_policy: str = Q_POLICY_()):
    """Complete analysis bundle used by the frontend (one request instead of many)."""
    rec, a = _run(request, inv_id, _cfg(max_hops, top_k, rapid_window_seconds, replay_policy), _aware(cutoff, "cutoff"))
    return _envelope(request, rec, a, case=summary_response(rec).model_dump(mode="json"), analysis=a)


@router.get("/api/investigations/{inv_id}/leads", tags=["intelligence"], responses=E)
def get_leads(inv_id: str, request: Request, k: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0),
              max_hops: int = Q_HOPS_(), rapid_window_seconds: float = Q_RAPID_(), cutoff: Optional[datetime] = None):
    rec, a = _run(request, inv_id, _cfg(max_hops, k, rapid_window_seconds, "observation"), _aware(cutoff, "cutoff"))
    leads = a["leads"]
    return _envelope(request, rec, a, total=len(leads), k=k, offset=offset, leads=leads[offset:offset + k],
                     ranking="heap top-k; ties: score desc, hops asc, node id asc",
                     score_type="transparent_priority_score_not_probability",
                     case_hypotheses=a["case_hypotheses"])


@router.get("/api/investigations/{inv_id}/evidence", tags=["intelligence"], responses=E)
def get_evidence(inv_id: str, request: Request, lead_id: Optional[str] = None, cutoff: Optional[datetime] = None):
    rec, a = _run(request, inv_id, AnalysisConfig(), _aware(cutoff, "cutoff"))
    ev, claims = a["evidence"], a["claims"]
    if lead_id:
        lead = next((L for L in a["leads"] if L["lead_id"] == lead_id), None)
        if lead is None:
            raise ApiError(404, "LEAD_NOT_FOUND", f"No lead '{lead_id}' in this analysis.")
        ids = set(lead["support_evidence_ids"] + lead["contradiction_evidence_ids"] + lead["context_evidence_ids"])
        ids |= {e["evidence_id"] for e in ev if e["provenance"].get("case_level") or e["kind"] == "origin_pattern"}
        ev = [e for e in ev if e["evidence_id"] in ids]
        claims = [c for c in claims if c["claim_id"].startswith(f"CLM-{lead_id}-")]
    return _envelope(request, rec, a, claims=claims, evidence=ev, findings=a["osint"]["findings"],
                     status_semantics=["Observed", "Calculated", "Source-reported", "Inferred", "Disputed", "Unresolved"])


@router.get("/api/investigations/{inv_id}/hypotheses", tags=["intelligence"], responses=E)
def get_hypotheses(inv_id: str, request: Request, lead_id: Optional[str] = None, cutoff: Optional[datetime] = None):
    rec, a = _run(request, inv_id, AnalysisConfig(), _aware(cutoff, "cutoff"))
    if lead_id:
        lead = next((L for L in a["leads"] if L["lead_id"] == lead_id), None)
        if lead is None:
            raise ApiError(404, "LEAD_NOT_FOUND", f"No lead '{lead_id}' in this analysis.")
        return _envelope(request, rec, a, hypotheses=lead["hypotheses"], falsification=lead["falsification"])
    return _envelope(request, rec, a, hypotheses=a["case_hypotheses"], falsification=a["case_falsification"])


@router.post("/api/investigations/{inv_id}/sensitivity", tags=["intelligence"], responses=E)
def post_sensitivity(inv_id: str, request: Request, body: Optional[SensitivityRequest] = Body(default=None)):
    """Re-runs the full engine with evidence removed/downgraded (or weights changed) and compares."""
    get_rec(request, inv_id)
    body = body or SensitivityRequest()
    cfg = to_config(body.config)
    # baseline = same configuration WITHOUT weight overrides and WITHOUT evidence overrides
    base_cfg = AnalysisConfig(**{**cfg.as_dict(), "weights": {}, "penalties": {}}).validate()
    rec, base = _run(request, inv_id, base_cfg, body.cutoff)
    known = {e["evidence_id"] for e in base["evidence"]}
    unknown = sorted((set(body.remove_evidence_ids) | set(body.downgrade_evidence_ids)) - known)
    if unknown:
        raise ApiError(422, "UNKNOWN_EVIDENCE_ID", "Some evidence ids are not part of this analysis.",
                       details={"unknown": unknown[:20]})
    docs = {d.doc_id for d in rec.osint.docs.values()}
    if set(body.remove_doc_ids) - docs:
        raise ApiError(422, "UNKNOWN_DOC_ID", "Some document ids are not part of this investigation.",
                       details={"unknown": sorted(set(body.remove_doc_ids) - docs)[:20]})
    ov = Overrides(remove_evidence_ids=body.remove_evidence_ids, downgrade_evidence_ids=body.downgrade_evidence_ids,
                   downgrade_factor=body.downgrade_factor, remove_doc_ids=body.remove_doc_ids)
    _, alt = _run(request, inv_id, cfg, body.cutoff, ov)
    cmp = compare_runs(base, alt)
    return _envelope(request, rec, alt, baseline_snapshot_id=base["meta"]["snapshot_id"], comparison=cmp,
                     sensitivity_request=body.model_dump(mode="json"), leads=alt["leads"][:cfg.top_k],
                     case_hypotheses=alt["case_hypotheses"], recomputed=True,
                     note="The full engine was re-run with the requested changes; this is a diagnostic of dependence "
                          "on specific evidence, not a test of truth.")


@router.get("/api/investigations/{inv_id}/replay", tags=["intelligence"], responses=E)
def get_replay(inv_id: str, request: Request, cutoff: Optional[datetime] = None, policy: str = Q_POLICY_(),
               top_k: int = Q_TOPK_()):
    """Recompute using only records available at ``cutoff`` (transfers by event time; documents by
    retrieval time under 'observation' policy or publication time under 'publication')."""
    get_rec(request, inv_id)  # unknown ids -> 404 before parameter checks (Person 1 contract)
    if cutoff is None:
        raise ApiError(422, "CUTOFF_REQUIRED", "Pass ?cutoff=<ISO-8601 UTC timestamp>, e.g. 2026-10-01T02:30:00Z.")
    _aware(cutoff, "cutoff")
    rec, a = _run(request, inv_id, _cfg(6, top_k, 600.0, policy), cutoff)
    return _envelope(request, rec, a, cutoff=a["meta"]["cutoff"], policy=policy,
                     leads=a["leads"][:top_k], case_hypotheses=a["case_hypotheses"],
                     timeline=a["timeline"], excluded={
                         "transfers_after_cutoff": a["graph_stats"]["events_excluded_by_cutoff"],
                         "documents_after_cutoff": a["osint"]["documents_excluded_by_cutoff"]},
                     graph_stats=a["graph_stats"], osint_documents=a["osint"]["documents"],
                     hindsight_guard="Records not available at the cutoff are excluded before any computation.")


@router.get("/api/investigations/{inv_id}/report", tags=["intelligence"], responses=E)
def get_report(inv_id: str, request: Request, format: Literal["json", "html"] = "json", cutoff: Optional[datetime] = None,
               top_k: int = Q_TOPK_()):
    rec, a = _run(request, inv_id, _cfg(6, top_k, 600.0, "observation"), _aware(cutoff, "cutoff"))
    case = {"investigation_id": rec.id, "chain_id": rec.chain_id, "scenario_id": rec.scenario_id,
            "start_address": rec.start_address, "scope": rec.scope, "simulation": rec.simulation,
            "data_mode": rec.data_mode, "sources": [s.model_dump(mode="json") for s in rec.sources]}
    bench = request.app.state.evaluation_cache.get("latest")
    rep = build_report(a, case, generated_at=now().isoformat(), benchmark={"summary": bench["summary"], "aggregate": bench["aggregate"]} if bench else None)
    fname = f"astra-casebook-{rec.id}-{a['meta']['snapshot_id']}"
    if format == "html":
        return HTMLResponse(render_html(rep), headers={"Content-Disposition": f'inline; filename="{fname}.html"',
                                                       "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    return JSONResponse(jsonable_encoder(_envelope(request, rec, a, report=rep) | {"generated_at": now()}),
                        headers={"Content-Disposition": f'inline; filename="{fname}.json"'})


@router.get("/api/investigations/{inv_id}/flow", tags=["graph"], responses=E)
def get_flow(inv_id: str, request: Request, cutoff: Optional[datetime] = None):
    rec, a = _run(request, inv_id, AnalysisConfig(), _aware(cutoff, "cutoff"))
    return _envelope(request, rec, a, flow=a["flow"])


@router.get("/api/investigations/{inv_id}/route", tags=["graph"], responses=E)
def get_route(inv_id: str, request: Request, target: str, method: Literal["hops", "fastest", "static"] = "hops",
              source: Optional[str] = None, hop_penalty_seconds: float = Query(3600.0, ge=0, le=30 * 86400),
              max_hops: int = Q_HOPS_()):
    """Shortest observed route: time-respecting min hops (BFS), fastest-forwarded route (Dijkstra,
    cost = hop penalty + dwell seconds), or time-ignoring hop count (static BFS)."""
    rec = get_rec(request, inv_id)
    for name, v in (("target", target), ("source", source)):
        if v is not None and not ADDRESS_RE.match(v):
            raise ApiError(422, "INVALID_ADDRESS", f"{name} must be 0x + 40 hex characters")
    src = normalize_address(source or rec.start_address or "")
    g = dsa.GraphIndex(_edges(rec))
    s, t = dsa.node_id(rec.chain_id, src), dsa.node_id(rec.chain_id, normalize_address(target))
    first = [e.ts for e in g.out_adj.get(s, [])]
    t0 = min(first) if first else None
    if method == "hops":
        r = dsa.min_hop_time_respecting(g, s, start_time=t0, max_hops=max_hops, targets={t}).get(t, dsa.PathResult(False, method="bfs_time_respecting"))
    elif method == "fastest":
        r = dsa.dijkstra_fastest_route(g, s, t, start_time=t0, hop_penalty_seconds=hop_penalty_seconds)
    else:
        r = dsa.bfs_min_hops_static(g, s, t, max_hops=max_hops)
    return {**meta(rec), "source": s, "target": t, "method": r.method, "found": r.found, "node_ids": r.node_ids,
            "event_ids": r.event_ids, "hops": r.hops, "cost": r.cost, "cost_breakdown": r.cost_breakdown,
            "truncated": r.truncated, "notes": ["A route is an observed transfer sequence, not proof that the same funds moved."]
            + (["Static BFS ignores time order; it can include impossible orderings."] if method == "static" else [])}


@router.get("/api/investigations/{inv_id}/subgraph", tags=["graph"], responses=E)
def get_subgraph(inv_id: str, request: Request, center: Optional[str] = None, max_hops: int = Query(3, ge=1, le=8),
                 max_nodes: int = Query(60, ge=2, le=500), direction: Literal["out", "in", "both"] = "both"):
    """Bounded BFS neighbourhood around ``center`` (default: case start) for the graph view."""
    rec = get_rec(request, inv_id)
    if center is not None and not ADDRESS_RE.match(center):
        raise ApiError(422, "INVALID_ADDRESS", "center must be 0x + 40 hex characters")
    c = dsa.node_id(rec.chain_id, normalize_address(center or rec.start_address or "0x" + "0" * 40))
    g = dsa.GraphIndex(_edges(rec))
    dist = dsa.bfs_neighborhood(g, c, max_hops=max_hops, max_nodes=max_nodes, direction=direction)
    keep = set(dist)
    edges = sorted((e for e in g.by_event.values() if e.u in keep and e.v in keep), key=lambda e: (e.ts, e.event_id))
    return {**meta(rec), "center": c, "found": c in g.nodes, "nodes": [{"node_id": n, "hops": d} for n, d in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))],
            "edge_ids": [e.event_id for e in edges][:5000], "truncated": len(dist) >= max_nodes,
            "parameters": {"max_hops": max_hops, "max_nodes": max_nodes, "direction": direction}}


def _edges(rec):
    from astra_intel.engine import edge_from_transfer
    return [edge_from_transfer(e) for e in rec.ingestion.events if e.status == "success"]


# ------------------------------------------------------------------ OSINT
@router.get("/api/investigations/{inv_id}/osint", tags=["osint"], responses=E)
def get_osint(inv_id: str, request: Request, cutoff: Optional[datetime] = None):
    rec, a = _run(request, inv_id, AnalysisConfig(), _aware(cutoff, "cutoff"))
    gh = request.app.state.github
    return _envelope(request, rec, a, sources=a["osint"]["sources"], documents=a["osint"]["documents"],
                     documents_unusable=a["osint"]["documents_unusable"], indicators=a["osint"]["indicators"],
                     findings=a["osint"]["findings"], match_summary=a["osint"]["match_summary"],
                     independence_groups=a["osint"]["independence_groups"],
                     live_adapters={"github_issues_search": gh.health()})


@router.post("/api/investigations/{inv_id}/osint/documents", status_code=201, tags=["osint"], responses=E)
def add_osint_document(inv_id: str, body: AnalystDocumentCreate, request: Request):
    """Attach analyst-supplied public-source text; the next analysis re-runs extraction and matching."""
    rec = get_rec(request, inv_id)
    doc = analyst_document(body, rec.data_mode)
    if len(rec.osint.docs) >= 500:
        raise ApiError(400, "TOO_MANY_DOCUMENTS", "Document limit (500) reached for this investigation.")
    created = rec.osint.add(doc)
    a = request.app.state.analysis.run(rec, AnalysisConfig())
    inds = [i for i in a["osint"]["indicators"] if i["doc_id"] == doc.doc_id]
    fnd = [f for f in a["osint"]["findings"] if f["doc_id"] == doc.doc_id]
    return _envelope(request, rec, a, document=doc.model_dump(mode="json"), created=created, indicators=inds, findings=fnd)


@router.post("/api/investigations/{inv_id}/osint/collect", tags=["osint"], responses=E)
def collect_osint(inv_id: str, request: Request, terms: Optional[list[str]] = Body(default=None, max_length=6),
                  max_items: int = Query(5, ge=1, le=20)):
    """Live collection from GitHub issue search for the case/lead addresses (LIVE investigations only)."""
    rec = get_rec(request, inv_id)
    if rec.data_mode == "synthetic":
        raise ApiError(409, "MODE_CONFLICT", "Live OSINT collection is disabled for synthetic investigations so that "
                       "synthetic and live evidence are never mixed. Create a live wallet investigation instead.")
    gh = request.app.state.github
    if not terms:
        a = request.app.state.analysis.run(rec, AnalysisConfig())
        terms = [rec.start_address] + [L["address"] for L in a["leads"][:4]]
    terms = [t for t in dict.fromkeys(terms) if t][:6]
    results = []
    for term in terms:
        try:
            raws = gh.search(term, max_items=max_items)
            added = sum(rec.osint.add(build_document(r, data_mode="real", extraction_method="github_issues_api_v1")) for r in raws)
            rec.osint.set_source_status(gh.source_id, name=gh.name, type="repository", data_mode="real", status="ok",
                                        last_retrieved_at=now(), error=None, url=gh.url, terms_note=gh.terms)
            results.append({"term": term, "status": "ok", "documents": len(raws), "new": added})
        except OSINTSourceError as exc:
            rec.osint.set_source_status(gh.source_id, name=gh.name, type="repository", data_mode="real",
                                        status="rate_limited" if exc.code == "OSINT_RATE_LIMITED" else "failed",
                                        error=exc.code, url=gh.url, terms_note=gh.terms)
            results.append({"term": term, "status": "error", "code": exc.code, "message": exc.message})
            if exc.code in ("OSINT_SOURCE_DISABLED", "OSINT_RATE_LIMITED"):
                break
    return {**meta(rec), "results": results, "source": gh.health(),
            "note": "Collected documents are unverified public text; matches are re-computed by the engine."}


# ------------------------------------------------------------------ simulation
@router.get("/api/simulations/defaults", tags=["simulation"])
def simulation_defaults():
    return {"defaults": SimulationCreate().model_dump(mode="json"),
            "kinds": ["incident", "benign", "insufficient"], "label": "SYNTHETIC / SIMULATED"}


@router.post("/api/simulations", status_code=201, tags=["simulation"], responses={422: {"model": ErrorEnvelope}})
def create_simulation(body: SimulationCreate, request: Request):
    """Generate a deterministic synthetic investigation and ingest it through the normal pipeline."""
    from astra_intel.simulation import generate
    st = request.app.state
    if len(st.store) >= st.settings.max_investigations:
        raise ApiError(429, "TOO_MANY_INVESTIGATIONS", "In-memory investigation limit reached; restart or raise "
                       "ASTRA_MAX_INVESTIGATIONS.", retryable=False)
    cfgd = body.model_dump()
    g = generate(cfgd)
    inv_id = f"inv-{g['scenario_id']}"
    existing = st.store.get(inv_id)
    if existing is not None:
        return summary_response(existing)
    retrieved = datetime.fromisoformat(g["metadata"]["start_time"].replace("Z", "+00:00"))
    src = SourceRef(provider="astra-simulator", data_mode="synthetic", retrieved_at=retrieved,
                    reference=f"{g['scenario_id']} (seed={body.seed})")
    nb = normalize_batch(g["native_rows"], g["erc20_rows"], 1, src)
    ing = IngestionResult(nb.events, nb.rejected, nb.duplicates_dropped, nb.conflicts, "synthetic", [src],
                          ["ALL RECORDS ARE SYNTHETIC / SIMULATED (generator astra-sim-v1). Addresses, hashes, documents "
                           "and timestamps are invented.", "Simulated incident; results demonstrate the pipeline, "
                           "not real-world attribution."])
    rec = build_record(inv_id, 1, g["scenario_id"], g["start_address"],
                       {"type": "simulation", "scenario_id": g["scenario_id"], "seed": body.seed,
                        "scenario_kind": body.scenario_kind}, ing)
    col = OSINTCollection()
    for d in g["documents"]:
        col.add(build_document(d, data_mode="synthetic", extraction_method="simulated_text_v1"))
    rec.osint = col
    rec.simulation = g["metadata"]
    st.store.put(rec)
    return summary_response(rec)


# ------------------------------------------------------------------ evaluation
@router.get("/api/evaluation", tags=["evaluation"])
def get_evaluation(request: Request, refresh: bool = False):
    """Measured benchmark on labelled synthetic scenarios (computed on demand, cached in memory)."""
    from ..services.evaluation import run_benchmark
    cache = request.app.state.evaluation_cache
    if refresh or "latest" not in cache:
        cache["latest"] = run_benchmark(list(request.app.state.settings.evaluation_seeds))
    return cache["latest"]


@router.get("/api/investigations", tags=["investigations"])
def list_investigations(request: Request):
    return {"generated_at": now(), "investigations": [
        {"investigation_id": r.id, "data_mode": r.data_mode, "scenario_id": r.scenario_id, "chain_id": r.chain_id,
         "start_address": r.start_address, "scope": r.scope, "event_count": len(r.ingestion.events)}
        for r in request.app.state.store.list()]}
