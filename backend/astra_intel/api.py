from __future__ import annotations
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field
from .evidence import EvidenceItem, Hypothesis, score_hypothesis
from .falsification import run_falsification
from .risk_scoring import score_lead, rank_leads
from .temporal import temporal_fingerprint
from .osint import extract_candidate_indicators, validate_indicator_match

# Stateless Person 2 utilities. The integrated backend mounts this router under /api/intel; the
# investigation workflow itself uses astra_intel.engine through /api/investigations/{id}/... .
router = APIRouter()


class ScoreRequest(BaseModel):
    case_id: str
    hypothesis_id: str
    title: str = "Investigation hypothesis"
    evidence: list[EvidenceItem] = Field(default_factory=list)


class CompareRequest(BaseModel):
    hypotheses: list[Hypothesis]


class FalsifyRequest(BaseModel):
    hypothesis: Hypothesis
    remove_evidence_ids: list[str] = Field(default_factory=list)
    downgrade_evidence_ids: list[str] = Field(default_factory=list)
    downgrade_factor: float = Field(default=0.5, ge=0.0, le=1.0)


@router.get("/health")
def health():
    return {"status": "ok", "service": "astra-person2-intelligence", "version": "0.1.0"}


@router.get("/model/info")
def model_info():
    return {
        "training_pipeline": "astra_intel.train",
        "expected_dataset": "Elliptic++ transaction-level CSV files",
        "model_artifact": "artifacts/model.joblib",
        "status": "not_trained_until_dataset_is_downloaded_and_training_is_run",
    }


@router.post("/score/evidence")
def score_evidence(req: ScoreRequest):
    hypothesis = Hypothesis(hypothesis_id=req.hypothesis_id, title=req.title, evidence=req.evidence)
    return {"case_id": req.case_id, **score_hypothesis(hypothesis)}


@router.post("/hypotheses/compare")
def compare_hypotheses(req: CompareRequest):
    if not req.hypotheses:
        raise HTTPException(status_code=400, detail="Provide at least one hypothesis.")
    results = [score_hypothesis(h) for h in req.hypotheses]
    results.sort(key=lambda x: x["heuristic_score"], reverse=True)
    return {"ranked_hypotheses": results, "ranking_type": "heuristic_not_probability"}


@router.post("/falsify")
def falsify(req: FalsifyRequest):
    return run_falsification(req.hypothesis, req.remove_evidence_ids,
                             req.downgrade_evidence_ids, req.downgrade_factor)


class LeadScoreRequest(BaseModel):
    entity_id: str
    signals: dict = Field(default_factory=dict)

class LeadRankRequest(BaseModel):
    leads: list[dict] = Field(default_factory=list)

class TemporalRequest(BaseModel):
    transfers: list[dict] = Field(default_factory=list)
    rapid_window_seconds: float = Field(default=600.0, gt=0)

class OSINTExtractRequest(BaseModel):
    text: str
    source_url: str
    source_name: str
    retrieved_at: str | None = None
    chain_hint: str = "unknown"

class IndicatorMatchRequest(BaseModel):
    indicator: dict
    observed_entity: str

@router.post("/score/lead")
def score_lead_endpoint(req: LeadScoreRequest):
    try:
        return score_lead(req.entity_id, req.signals)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/score/rank")
def rank_leads_endpoint(req: LeadRankRequest):
    try:
        return {"ranked_leads": rank_leads(req.leads), "ranking_type": "transparent_priority_score_not_probability"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/features/temporal")
def temporal_endpoint(req: TemporalRequest):
    try:
        return temporal_fingerprint(req.transfers, rapid_window_seconds=req.rapid_window_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/osint/extract")
def osint_extract_endpoint(req: OSINTExtractRequest):
    try:
        return extract_candidate_indicators(req.text, source_url=req.source_url, source_name=req.source_name, retrieved_at=req.retrieved_at, chain_hint=req.chain_hint)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.post("/osint/match")
def osint_match_endpoint(req: IndicatorMatchRequest):
    return validate_indicator_match(req.indicator, req.observed_entity)


# Stand-alone app kept for backwards compatibility: `uvicorn astra_intel.api:app`
app = FastAPI(title="ASTRA Person 2 Intelligence API (stateless utilities)", version="0.2.0",
              description="Evidence-aware heuristic and falsification endpoints. The integrated application "
                          "serves these under /api/intel.")
app.include_router(router)
