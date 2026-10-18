"""Ingestion orchestration: retrieve -> validate/normalize -> (graph built elsewhere)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ...schemas.common import DataMode, SourceRef
from ...schemas.transaction import NormalizedTransfer
from .normalizer import NormalizationResult, normalize_batch
from .providers import EtherscanProvider, WalletQuery


class ScenarioFile(BaseModel):
    scenario_id: str
    name: str
    description: str
    dataset_version: str
    data_mode: DataMode
    synthetic: bool
    synthetic_notice: str
    chain_id: int
    fixture_retrieved_at: datetime
    start_address: str
    native_rows: list[dict]
    erc20_rows: list[dict]
    expected: dict
    limitations: list[str]


def load_scenarios(data_dir: Path) -> dict[str, ScenarioFile]:
    out: dict[str, ScenarioFile] = {}
    for p in sorted((data_dir / "scenarios").glob("*.json")):
        sc = ScenarioFile.model_validate(json.loads(p.read_text()))
        if sc.synthetic and sc.data_mode != "synthetic":
            raise ValueError(f"{p.name}: synthetic scenario must have data_mode 'synthetic'")
        out[sc.scenario_id] = sc
    return out


@dataclass
class IngestionResult:
    events: list[NormalizedTransfer]
    rejected: list[dict]
    duplicates_dropped: int
    conflicts: list[str]
    data_mode: DataMode
    sources: list[SourceRef]
    limitations: list[str] = field(default_factory=list)


def _wrap(res: NormalizationResult, mode: DataMode, source: SourceRef, lim: list[str]) -> IngestionResult:
    return IngestionResult(res.events, res.rejected, res.duplicates_dropped, res.conflicts, mode, [source], lim)


def ingest_scenario(sc: ScenarioFile) -> IngestionResult:
    src = SourceRef(provider="astra-fixture", data_mode="synthetic",
                    retrieved_at=sc.fixture_retrieved_at, reference=f"{sc.scenario_id}@{sc.dataset_version}")
    res = normalize_batch(sc.native_rows, sc.erc20_rows, sc.chain_id, src)
    return _wrap(res, "synthetic", src, [sc.synthetic_notice, *sc.limitations])


def ingest_wallet(provider: EtherscanProvider, q: WalletQuery) -> IngestionResult:
    pr = provider.fetch_wallet_transfers(q)  # raises ProviderError; never falls back to fixtures
    mode: DataMode = "cached" if pr.from_cache else "real"
    src = SourceRef(provider=pr.provider, data_mode=mode, retrieved_at=pr.retrieved_at, reference=pr.reference)
    res = normalize_batch(pr.native_rows, pr.erc20_rows, q.chain_id, src)
    lim = [
        "Wallet query returns only the queried address's own transfers (one-hop neighbourhood); "
        "expand by creating further bounded investigations.",
        "Indexed provider coverage is limited to normal transactions and ERC-20 transfer events; "
        "internal (contract-to-contract) ETH transfers are not included.",
        "Graph connectivity and timing are observed relationships, not proof of common ownership.",
    ]
    if mode == "cached":
        lim.append(f"Served from cache; provider data was retrieved at {pr.retrieved_at.isoformat()}.")
    return _wrap(res, mode, src, lim)
