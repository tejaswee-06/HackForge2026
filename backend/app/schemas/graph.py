"""Graph / path / investigation response models."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import DataMode, ResponseMeta, SourceRef
from .transaction import ADDRESS_RE, NormalizedTransfer, normalize_address


class GraphNode(BaseModel):
    node_id: str = Field(description="'<chain_id>:<lowercase address>'")
    chain_id: int
    address: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    in_transfer_count: int
    out_transfer_count: int
    gross_in_raw: dict[str, str] = Field(description="asset_id -> sum of raw incoming edge values (gross, NOT unique value)")
    gross_out_raw: dict[str, str]


class GraphEdge(BaseModel):
    edge_id: str = Field(description="== event_id of the underlying observed transfer")
    source: str
    target: str
    chain_id: int
    transaction_hash: str
    log_index: Optional[int] = None
    block_number: Optional[int] = None
    timestamp: datetime
    asset_id: str
    asset_type: str
    asset_symbol: Optional[str] = None
    token_contract: Optional[str] = None
    token_decimals: Optional[int] = None
    raw_value: str
    normalized_value: Optional[str] = None
    status: str
    event_type: str
    provenance: SourceRef


class GraphResponse(ResponseMeta):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    filters: dict[str, Any]
    stats: dict[str, Any]
    notes: list[str] = []


class PathHop(BaseModel):
    event_id: str
    from_node: str
    to_node: str
    transaction_hash: str
    log_index: Optional[int] = None
    timestamp: datetime
    raw_value: str
    normalized_value: Optional[str] = None


class TracePath(BaseModel):
    path_id: str
    asset_id: str
    node_ids: list[str]
    event_ids: list[str]
    hops: int
    start_time: datetime
    end_time: datetime
    bottleneck_raw: str = Field(description="min raw edge value along the path: an UPPER BOUND on value that could have traversed it. Not a claim that those funds did.")
    bottleneck_normalized: Optional[str] = None
    terminated_reason: Literal["leaf", "max_depth", "cycle_blocked"]
    edges: list[PathHop]


class PathsResponse(ResponseMeta):
    start_address: str
    start_found: bool
    parameters: dict[str, Any]
    paths: list[TracePath]
    truncated_by_limit: bool
    truncated_by_budget: bool
    notes: list[str] = []


class TransactionsResponse(ResponseMeta):
    total: int
    limit: int
    offset: int
    transactions: list[NormalizedTransfer]


class InvestigationCreate(BaseModel):
    """Either a known scenario, or a bounded wallet query (exactly one)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"scenario_id": "ghost_attacks_synthetic_v1"},
                {
                    "chain_id": 1,
                    "address": "0x00000000000000000000000000000000000a0001",
                    "max_transfers": 100,
                },
            ]
        },
    )
    scenario_id: Optional[str] = None
    chain_id: Optional[int] = None
    address: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_transfers: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def _exactly_one(self):
        wallet = self.chain_id is not None or self.address is not None
        if self.scenario_id and wallet:
            raise ValueError("provide either scenario_id or (chain_id + address), not both")
        if not self.scenario_id:
            if self.chain_id is None or self.address is None:
                raise ValueError("provide scenario_id, or both chain_id and address")
            if not ADDRESS_RE.match(self.address):
                raise ValueError("address must be 0x + 40 hex characters")
            self.address = normalize_address(self.address)
            for t in (self.start_time, self.end_time):
                if t is not None and t.tzinfo is None:
                    raise ValueError("start_time/end_time must be timezone-aware")
            if self.start_time and self.end_time and self.start_time > self.end_time:
                raise ValueError("start_time must be <= end_time")
        return self


class InvestigationResponse(ResponseMeta):
    status: Literal["ready"]
    chain_id: int
    scenario_id: Optional[str] = None
    start_address: Optional[str] = None
    scope: dict[str, Any]
    summary: dict[str, Any]
    source_status: dict[str, Any]
    created_at: datetime


class ScenarioInfo(BaseModel):
    scenario_id: str
    name: str
    description: str
    data_mode: DataMode
    synthetic: bool
    chain_id: int
    dataset_version: str
    start_address: str
    event_count: int
    investigation_id: str = Field(description="Pre-created investigation id for one-click demo")
    limitations: list[str]


class ScenariosResponse(BaseModel):
    generated_at: datetime
    analysis_version: str
    scenarios: list[ScenarioInfo]
