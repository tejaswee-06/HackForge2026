import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.ingestion.service import ingest_scenario, load_scenarios  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SCEN_ID = "ghost_attacks_synthetic_v1"
INV = f"inv-{SCEN_ID}"
SECRET = "SECRET-KEY-DO-NOT-LEAK-123"
WALLET = "0x" + "0" * 36 + "c0de"


@pytest.fixture(scope="session")
def scenario():
    return load_scenarios(DATA_DIR)[SCEN_ID]


@pytest.fixture(scope="session")
def expected(scenario):
    return scenario.expected


@pytest.fixture()
def ingested(scenario):
    return ingest_scenario(scenario)


def settings(**kw) -> Settings:
    base = dict(data_dir=DATA_DIR, etherscan_api_key=None, provider_backoff_s=0.0, cache_ttl_s=300)
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def client():
    """Offline: no API key configured."""
    return TestClient(create_app(settings()))


def etherscan_handler(native=None, erc20=None, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(dict(request.url.params))
        action = request.url.params["action"]
        rows = (native if action == "txlist" else erc20) or []
        if not rows:
            return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})
    return handler


def live_client(handler, **kw) -> TestClient:
    app = create_app(settings(etherscan_api_key=SECRET, **kw), etherscan_transport=httpx.MockTransport(handler),
                     sleep=lambda s: None)
    return TestClient(app)
