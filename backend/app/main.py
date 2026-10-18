import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from astra_intel.api import router as intel_stateless_router

from .api.intel_routes import router as intel_router
from .api.routes import attach_scenario_osint, build_record, router, scenario_investigation_id
from .config import ANALYSIS_VERSION, Settings
from .errors import ApiError
from .services.analysis import AnalysisService
from .services.ingestion.providers import EtherscanProvider
from .services.osint_ingest.service import GitHubIssuesSource
from .services.ingestion.service import ingest_scenario, load_scenarios
from .store import InvestigationStore

log = logging.getLogger("astra")


def _envelope(status: int, code: str, message: str, *, retryable=False, degraded=False, provider=None, details=None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "retryable": retryable, "degraded_mode": degraded,
        "provider": provider, "details": details}})


def create_app(settings: Optional[Settings] = None, *, etherscan_transport: Optional[httpx.BaseTransport] = None,
               sleep=None, github_transport: Optional[httpx.BaseTransport] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="ASTRA - Quantitative OSINT Blockchain Intelligence API", version=ANALYSIS_VERSION,
        description="Person 1 data/graph backend + Person 2 intelligence engine (OSINT, DSA, ranking, hypotheses, "
                    "sensitivity, replay, reports). Data mode is always one of synthetic | real | cached. "
                    "Scores are prioritisation aids, not probabilities; graph connectivity is NOT proof of ownership.")
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.state.settings = settings
    kw = {"sleep": sleep} if sleep else {}
    app.state.provider = EtherscanProvider(settings, transport=etherscan_transport, **kw)
    app.state.store = InvestigationStore()
    app.state.analysis = AnalysisService()
    app.state.evaluation_cache = {}
    app.state.github = GitHubIssuesSource(enabled=settings.osint_github_enabled, token=settings.github_token,
                                          timeout_s=settings.provider_timeout_s, transport=github_transport, **kw)
    app.state.scenarios = load_scenarios(settings.data_dir)
    for sc in app.state.scenarios.values():  # pre-create one-click demo cases (offline, deterministic)
        app.state.store.put(attach_scenario_osint(build_record(
            scenario_investigation_id(sc.scenario_id), sc.chain_id, sc.scenario_id, sc.start_address,
            {"type": "scenario", "scenario_id": sc.scenario_id, "dataset_version": sc.dataset_version},
            ingest_scenario(sc)), settings.data_dir))
    app.include_router(router)
    app.include_router(intel_router)
    app.include_router(intel_stateless_router, prefix="/api/intel", tags=["intel-stateless (Person 2)"])

    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return _envelope(e.status, e.code, e.message, retryable=e.retryable, degraded=e.degraded_mode,
                         provider=e.provider, details=e.details)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        det = [{"loc": [str(x) for x in err["loc"]], "msg": err["msg"], "type": err["type"]} for err in e.errors()]
        return _envelope(422, "VALIDATION_ERROR", "Request validation failed.", details=det)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        return _envelope(e.status_code, "NOT_FOUND" if e.status_code == 404 else "HTTP_ERROR", str(e.detail))

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        log.exception("unhandled error")  # message never includes request secrets
        return _envelope(500, "INTERNAL_ERROR", "Unexpected server error.")

    return app


app = create_app()
