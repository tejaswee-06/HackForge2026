import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request

from astra_intel.engine import ENGINE_VERSION

from ..config import ANALYSIS_VERSION, SERVICE_NAME
from ..errors import PROVIDER_HTTP, ApiError
from ..schemas.common import ErrorEnvelope
from ..schemas.graph import (GraphResponse, InvestigationCreate, InvestigationResponse, PathsResponse,
                             ScenarioInfo, ScenariosResponse, TransactionsResponse)
from ..schemas.transaction import ADDRESS_RE, normalize_address
from ..services.ingestion.providers import ProviderError, WalletQuery
from ..services.ingestion.service import ingest_scenario, ingest_wallet
from ..services.osint_ingest.service import load_fixture_documents
from ..services.graph_engine.engine import TransferGraph
from ..store import InvestigationRecord

router = APIRouter()
ERRS = {404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}, 503: {"model": ErrorEnvelope}}


def now() -> datetime:
    return datetime.now(timezone.utc)


def scenario_investigation_id(scenario_id: str) -> str:
    return f"inv-{scenario_id}"


def wallet_investigation_id(q: InvestigationCreate) -> str:
    key = f"{q.chain_id}|{q.address}|{q.start_time}|{q.end_time}|{q.max_transfers}"
    return "inv-w-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def meta(rec: InvestigationRecord) -> dict:
    return dict(investigation_id=rec.id, data_mode=rec.data_mode, analysis_version=ANALYSIS_VERSION,
                generated_at=now(), data_cutoff=rec.data_cutoff, limitations=rec.limitations, sources=rec.sources)


def build_record(inv_id, chain_id, scenario_id, start_address, scope, ing) -> InvestigationRecord:
    return InvestigationRecord(id=inv_id, chain_id=chain_id, scenario_id=scenario_id, start_address=start_address,
                               scope=scope, data_mode=ing.data_mode, ingestion=ing,
                               graph=TransferGraph(ing.events), created_at=now(), limitations=ing.limitations)


def attach_scenario_osint(rec: InvestigationRecord, data_dir) -> InvestigationRecord:
    """Load the scenario's labelled synthetic OSINT documents (if any) into the investigation."""
    if rec.scenario_id:
        col = load_fixture_documents(data_dir, rec.scenario_id)
        if col is not None:
            rec.osint = col
    return rec


def summary_response(rec: InvestigationRecord) -> InvestigationResponse:
    g, ing = rec.graph, rec.ingestion
    return InvestigationResponse(
        **meta(rec), status="ready", chain_id=rec.chain_id, scenario_id=rec.scenario_id,
        start_address=rec.start_address, scope=rec.scope, created_at=rec.created_at,
        summary={"event_count": len(ing.events), "node_count": g.node_count, "edge_count": g.edge_count,
                 "weakly_connected_components": g.components(),
                 "assets": [{"asset_id": a, "edge_count": n} for a, n in g.asset_edge_counts().items()],
                 "excluded_non_success_events": len(g.excluded_non_success),
                 "duplicates_dropped": ing.duplicates_dropped, "duplicate_conflicts": ing.conflicts,
                 "rejected_rows": len(ing.rejected), "rejected_detail": ing.rejected[:20],
                 "osint_documents": len(rec.osint.docs), "osint_sources": len(rec.osint.sources)},
        source_status={"providers": [{"provider": s.provider, "data_mode": s.data_mode,
                                      "retrieved_at": s.retrieved_at, "reference": s.reference} for s in rec.sources],
                       "degraded_mode": False})


def get_rec(request: Request, inv_id: str) -> InvestigationRecord:
    rec = request.app.state.store.get(inv_id)
    if rec is None:
        raise ApiError(404, "INVESTIGATION_NOT_FOUND", f"No investigation with id '{inv_id}'.")
    return rec


# ---------------------------------------------------------------- health / scenarios
@router.get("/health", tags=["system"])
def health(request: Request):
    st = request.app.state
    eth = st.provider.health()
    degraded = not eth["configured"] or eth["last_error_code"] is not None
    return {
        "status": "ok" if st.scenarios else "unavailable", "service": SERVICE_NAME,
        "analysis_version": ANALYSIS_VERSION, "generated_at": now(),
        "default_data_mode": "synthetic", "offline_demo_available": bool(st.scenarios),
        "scenarios_loaded": len(st.scenarios), "degraded_mode": degraded,
        "degraded_reason": None if not degraded else (
            "Live provider not configured; only synthetic scenarios can be served." if not eth["configured"]
            else f"Live provider last error: {eth['last_error_code']}"),
        "providers": {"fixture": {"status": "ok", "data_mode": "synthetic"}, "etherscan": eth,
                      "github_issues_search": st.github.health(),
                      "simulator": {"status": "ok", "data_mode": "synthetic", "generator": "astra-sim-v1"}},
        "engine": {"status": "ok", "version": ENGINE_VERSION, "trained_model_loaded": False},
        "investigations_in_memory": len(st.store),
    }


@router.get("/api/scenarios", response_model=ScenariosResponse, tags=["scenarios"])
def list_scenarios(request: Request):
    items = [ScenarioInfo(scenario_id=s.scenario_id, name=s.name, description=s.description, data_mode=s.data_mode,
                          synthetic=s.synthetic, chain_id=s.chain_id, dataset_version=s.dataset_version,
                          start_address=s.start_address, event_count=len(s.native_rows) + len(s.erc20_rows),
                          investigation_id=scenario_investigation_id(s.scenario_id), limitations=s.limitations)
             for s in request.app.state.scenarios.values()]
    return ScenariosResponse(generated_at=now(), analysis_version=ANALYSIS_VERSION, scenarios=items)


# ---------------------------------------------------------------- investigations
@router.post("/api/investigations", response_model=InvestigationResponse, status_code=201, tags=["investigations"],
             responses={400: {"model": ErrorEnvelope}, 502: {"model": ErrorEnvelope}, **ERRS})
def create_investigation(body: InvestigationCreate, request: Request):
    st = request.app.state
    if body.scenario_id:
        sc = st.scenarios.get(body.scenario_id)
        if sc is None:
            raise ApiError(404, "SCENARIO_NOT_FOUND", f"Unknown scenario '{body.scenario_id}'.",
                           details={"available": sorted(st.scenarios)})
        rec = attach_scenario_osint(build_record(
            scenario_investigation_id(sc.scenario_id), sc.chain_id, sc.scenario_id, sc.start_address,
            {"type": "scenario", "scenario_id": sc.scenario_id, "dataset_version": sc.dataset_version},
            ingest_scenario(sc)), st.settings.data_dir)
    else:
        if len(st.store) >= st.settings.max_investigations:
            raise ApiError(429, "TOO_MANY_INVESTIGATIONS", "In-memory investigation limit reached.")
        if body.chain_id not in st.settings.supported_chains:
            raise ApiError(400, "UNSUPPORTED_CHAIN", f"chain_id {body.chain_id} is not supported.",
                           details={"supported_chains": list(st.settings.supported_chains)})
        if body.max_transfers > st.settings.max_wallet_transfers:
            raise ApiError(400, "UNBOUNDED_REQUEST", f"max_transfers exceeds limit {st.settings.max_wallet_transfers}.")
        try:
            ing = ingest_wallet(st.provider, WalletQuery(chain_id=body.chain_id, address=body.address,
                                                          max_transfers=body.max_transfers))
        except ProviderError as exc:
            raise ApiError(PROVIDER_HTTP.get(exc.code, 502), exc.code, exc.message, retryable=exc.retryable,
                           degraded_mode=True, provider=exc.provider,
                           details={"hint": "Use POST /api/investigations with a synthetic scenario_id for the offline demo."})
        if body.start_time or body.end_time:
            # Etherscan returns a bounded page before this application-level timestamp filter.
            # It does not guarantee that the page covers the requested historical window. Keep
            # the limitation explicit instead of implying a complete time-range query.
            ing.events = [e for e in ing.events if (body.start_time is None or e.timestamp >= body.start_time)
                          and (body.end_time is None or e.timestamp <= body.end_time)]
            ing.limitations.append(
                "Time filtering was applied to the provider's bounded transfer sample after retrieval; "
                "the result is not guaranteed to cover every transfer in the requested time window. "
                "Use a provider/query method with block-range support for exhaustive historical windows."
            )
        rec = build_record(wallet_investigation_id(body), body.chain_id, None, body.address,
                           {"type": "wallet", "chain_id": body.chain_id, "address": body.address,
                            "max_transfers": body.max_transfers, "start_time": body.start_time,
                            "end_time": body.end_time,
                            "time_filter_applied_after_fetch": bool(body.start_time or body.end_time)}, ing)
    st.store.put(rec)
    return summary_response(rec)


@router.get("/api/investigations/{inv_id}", response_model=InvestigationResponse, tags=["investigations"],
            responses={404: {"model": ErrorEnvelope}})
def get_investigation(inv_id: str, request: Request):
    return summary_response(get_rec(request, inv_id))


@router.get("/api/investigations/{inv_id}/graph", response_model=GraphResponse, tags=["graph"],
            responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}})
def get_graph(inv_id: str, request: Request, asset_id: Optional[str] = None,
              start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
              max_edges: int = Query(2000, ge=1, le=5000)):
    rec = get_rec(request, inv_id)
    nodes, edges, truncated = rec.graph.export(asset_id, start_time, end_time, max_edges)
    notes = []
    if not edges:
        notes.append("No transfers match the scope/filters; the graph is empty.")
    if truncated:
        notes.append(f"Edge list truncated to max_edges={max_edges}; narrow filters to see the rest.")
    return GraphResponse(**meta(rec), nodes=nodes, edges=edges,
                         filters={"asset_id": asset_id, "start_time": start_time, "end_time": end_time},
                         stats={"node_count": len(nodes), "edge_count": len(edges), "truncated": truncated,
                                "excluded_non_success_events": len(rec.graph.excluded_non_success)}, notes=notes)


@router.get("/api/investigations/{inv_id}/paths", response_model=PathsResponse, tags=["graph"],
            responses={404: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}})
def get_paths(inv_id: str, request: Request, start: Optional[str] = Query(None, description="Start address; defaults to the case start address"),
              max_depth: int = Query(4, ge=1, le=10), max_results: int = Query(50, ge=1, le=500),
              asset_id: Optional[str] = None, start_time: Optional[datetime] = None,
              end_time: Optional[datetime] = None, enforce_time_order: bool = True):
    rec = get_rec(request, inv_id)
    addr = start or rec.start_address
    if addr is None:
        raise ApiError(422, "START_ADDRESS_REQUIRED", "This investigation has no default start address; pass ?start=0x...")
    if not ADDRESS_RE.match(addr):
        raise ApiError(422, "INVALID_ADDRESS", "start must be 0x + 40 hex characters")
    addr = normalize_address(addr)
    tr = rec.graph.trace_paths(rec.chain_id, addr, max_depth=max_depth, max_results=max_results, asset_id=asset_id,
                               start_time=start_time, end_time=end_time, enforce_time_order=enforce_time_order)
    notes = ["Paths are observed transfer sequences (single asset, no repeated wallet"
             + (", time-ordered" if enforce_time_order else "") + "). They do not prove common ownership.",
             "bottleneck_raw is the smallest edge value on the path (an upper bound), not an amount proven to have traversed it."]
    if not tr.start_found:
        notes.append("Start address does not appear in this investigation's graph.")
    elif not tr.paths:
        notes.append("No outgoing transfers match the filters from the start address.")
    return PathsResponse(**meta(rec), start_address=addr, start_found=tr.start_found,
                         parameters={"max_depth": max_depth, "max_results": max_results, "asset_id": asset_id,
                                     "start_time": start_time, "end_time": end_time,
                                     "enforce_time_order": enforce_time_order},
                         paths=tr.paths, truncated_by_limit=tr.truncated_by_limit,
                         truncated_by_budget=tr.truncated_by_budget, notes=notes)


@router.get("/api/investigations/{inv_id}/transactions", response_model=TransactionsResponse, tags=["graph"],
            responses={404: {"model": ErrorEnvelope}})
def get_transactions(inv_id: str, request: Request, asset_id: Optional[str] = None,
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                     include_non_success: bool = False):
    """Canonical normalized records - the interface Person 2 consumes (no provider-specific shapes)."""
    rec = get_rec(request, inv_id)
    evs = [e for e in rec.ingestion.events if (asset_id is None or e.asset_id == asset_id)
           and (include_non_success or e.status == "success")]
    return TransactionsResponse(**meta(rec), total=len(evs), limit=limit, offset=offset,
                                transactions=evs[offset:offset + limit])


# Person 2 endpoints (leads, evidence, sensitivity, replay, report, ...) are implemented in
# app/api/intel_routes.py on top of astra_intel.engine. The former 501 stubs were removed.
