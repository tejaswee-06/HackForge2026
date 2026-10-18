"""Shared contracts for OSINT documents, analysis configuration, sensitivity, replay and simulation.

These are the boundary schemas between Person 1 (source connectivity / ingestion), Person 2
(intelligence engine, ``astra_intel``) and Person 3 (frontend). Timestamps are timezone-aware UTC;
``None`` means unknown (never zero); confidence-like numbers are rubric values in [0,1], not
probabilities.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import DataMode, ResponseMeta

SourceType = Literal["threat_report", "repository", "label_registry", "analyst_note", "forum", "threat_feed", "other"]
MAX_DOC_CHARS = 200_000


def _aware(v: Optional[datetime]) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return v


def _http_url(v: str) -> str:
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    return v


class SourceDocument(BaseModel):
    """A collected public-source document with full provenance (Person 1 output, Person 2 input)."""

    model_config = ConfigDict(extra="forbid")
    doc_id: str
    source_id: str
    source_name: str
    source_type: SourceType
    publisher_group: Optional[str] = None
    url: str
    title: Optional[str] = None
    published_at: Optional[datetime] = Field(default=None, description="Source publication time; None = unknown")
    retrieved_at: datetime = Field(description="When ASTRA/the analyst retrieved the content")
    text: str = Field(default="", max_length=MAX_DOC_CHARS)
    content_hash: str
    extraction_method: str
    reliability: float = Field(ge=0, le=1, description="Source reliability RUBRIC value, not a probability")
    reliability_rationale: str
    data_mode: DataMode
    collection_status: Literal["collected", "failed", "partial"] = "collected"
    error: Optional[str] = None
    terms_note: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _u(cls, v):
        return _http_url(v)

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def _t(cls, v):
        return _aware(v)


class AnalystDocumentCreate(BaseModel):
    """Analyst-supplied public-source text (copied from a permitted source the analyst opened)."""

    model_config = ConfigDict(extra="forbid")
    source_name: str = Field(min_length=1, max_length=200)
    source_type: SourceType = "other"
    url: str
    title: Optional[str] = Field(default=None, max_length=300)
    published_at: Optional[datetime] = None
    text: str = Field(min_length=1, max_length=MAX_DOC_CHARS)
    reliability: float = Field(default=0.5, ge=0, le=1)
    reliability_rationale: str = Field(default="Analyst-supplied; reliability rubric set by the analyst.", max_length=500)
    publisher_group: Optional[str] = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def _u(cls, v):
        return _http_url(v)

    @field_validator("published_at")
    @classmethod
    def _t(cls, v):
        return _aware(v)


class AnalysisConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_hops: int = Field(default=6, ge=1, le=12)
    max_candidates: int = Field(default=300, ge=1, le=5000)
    top_k: int = Field(default=10, ge=1, le=100)
    rapid_window_seconds: float = Field(default=600.0, gt=0, le=30 * 86400)
    freshness_half_life_days: float = Field(default=90.0, gt=0, le=3650)
    stale_after_days: float = Field(default=180.0, gt=0, le=3650)
    hop_penalty_seconds: float = Field(default=3600.0, ge=0, le=30 * 86400)
    replay_policy: Literal["observation", "publication"] = "observation"
    weights: dict[str, float] = Field(default_factory=dict)
    penalties: dict[str, float] = Field(default_factory=dict)


class SensitivityRequest(BaseModel):
    """Remove/downgrade evidence, drop documents, or change weights; the engine recomputes everything."""

    model_config = ConfigDict(extra="forbid")
    remove_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    downgrade_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    downgrade_factor: float = Field(default=0.5, ge=0, le=1)
    remove_doc_ids: list[str] = Field(default_factory=list, max_length=200)
    config: Optional[AnalysisConfigModel] = None
    cutoff: Optional[datetime] = None

    @field_validator("cutoff")
    @classmethod
    def _t(cls, v):
        return _aware(v)


class SimulationCreate(BaseModel):
    """Deterministic synthetic investigation generator configuration (see SIMULATION_AND_LIVE_MODES.md)."""

    model_config = ConfigDict(extra="forbid")
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    scenario_kind: Literal["incident", "benign", "insufficient"] = "incident"
    depth: int = Field(default=4, ge=1, le=8)
    branching: int = Field(default=3, ge=1, le=6)
    noise_addresses: int = Field(default=12, ge=0, le=5000)
    noise_transfers: int = Field(default=20, ge=0, le=50000)
    disconnected_components: int = Field(default=2, ge=0, le=50)
    include_cycle: bool = True
    include_self_loop: bool = True
    duplicate_records: int = Field(default=2, ge=0, le=100)
    failed_transactions: int = Field(default=1, ge=0, le=100)
    include_token_leg: bool = True
    repeated_edges: int = Field(default=2, ge=0, le=100)
    base_amount_eth: int = Field(default=420, ge=1, le=10**6)
    start_time: datetime = datetime.fromisoformat("2026-10-01T02:14:00+00:00")
    window_hours: float = Field(default=6.0, gt=0, le=24 * 365)
    osint_incident_report: bool = True
    osint_service_label: bool = True
    osint_contradiction: bool = True
    osint_stale: bool = True
    osint_missing_timestamp: bool = True
    osint_ambiguous: bool = True
    osint_duplicate: bool = True
    osint_outage: bool = True

    @field_validator("start_time")
    @classmethod
    def _t(cls, v):
        return _aware(v)


class AnalysisResponse(ResponseMeta):
    """Envelope for analysis-derived responses (leads, evidence, hypotheses, replay, report)."""

    data_label: str
    snapshot_id: str
    config_hash: str
    analysis: dict[str, Any]
