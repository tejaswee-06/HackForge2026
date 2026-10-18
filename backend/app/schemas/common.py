"""Shared response metadata, provenance and the error envelope (blueprint §8.3 / §13.3)."""
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DataMode = Literal["synthetic", "real", "cached"]


class SourceRef(BaseModel):
    """Where a record came from. Attached to every normalized transfer."""

    model_config = ConfigDict(extra="forbid")
    provider: str
    data_mode: DataMode
    retrieved_at: datetime
    reference: Optional[str] = Field(default=None, description="Scenario id/version or provider endpoint (never a key).")


class ResponseMeta(BaseModel):
    """Fields every API response carries."""

    investigation_id: Optional[str] = None
    data_mode: DataMode
    analysis_version: str
    generated_at: datetime
    data_cutoff: Optional[datetime] = Field(default=None, description="Latest event timestamp included in the data.")
    limitations: list[str] = []
    sources: list[SourceRef] = []


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    degraded_mode: bool = False
    provider: Optional[str] = None
    details: Optional[Any] = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
