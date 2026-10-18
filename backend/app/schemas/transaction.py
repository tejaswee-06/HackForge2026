"""Canonical normalized transfer (blueprint §7.1/§7.4).

Rules enforced here:
* addresses/hashes lower-cased (identity preserved; EIP-55 checksum casing is presentation only)
* raw_value is an exact decimal-integer *string*; never float
* normalized_value is derived with integer arithmetic ONLY when token decimals are known
* unknown (None) is distinct from zero ("0")
* timestamps are timezone-aware UTC
"""
import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .common import SourceRef

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
TXHASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def normalize_address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.match(value):
        raise ValueError("address must be 0x + 40 hex characters")
    return value.lower()


def format_units(raw: int, decimals: int) -> str:
    """Exact fixed-point rendering with integer math (no float, no Decimal rounding)."""
    if decimals == 0:
        return str(raw)
    whole, frac = divmod(raw, 10**decimals)
    return f"{whole}.{frac:0{decimals}d}"


class NormalizedTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    chain_id: int
    transaction_hash: str
    log_index: Optional[int] = None
    block_number: Optional[int] = None
    timestamp: datetime
    from_address: str
    to_address: str
    asset_id: str  # "<chain>:native" or "<chain>:erc20:<contract>"; never mixed across assets
    asset_type: Literal["native", "erc20"]
    asset_symbol: Optional[str] = None  # provider-reported, informational only
    token_contract: Optional[str] = None
    token_decimals: Optional[int] = None
    raw_value: str
    normalized_value: Optional[str] = None
    status: Literal["success", "failed", "unknown"]
    event_type: Literal["native_transfer", "erc20_transfer"]
    source: SourceRef

    @field_validator("from_address", "to_address", "token_contract")
    @classmethod
    def _addr(cls, v):
        return None if v is None else normalize_address(v)

    @field_validator("transaction_hash")
    @classmethod
    def _hash(cls, v):
        if not TXHASH_RE.match(v):
            raise ValueError("transaction_hash must be 0x + 64 hex characters")
        return v.lower()

    @field_validator("raw_value")
    @classmethod
    def _raw(cls, v):
        if not isinstance(v, str) or not v.isascii() or not v.isdigit():
            raise ValueError("raw_value must be a non-negative integer string")
        return str(int(v))  # canonical: strips leading zeros, preserves exactness

    @field_validator("chain_id")
    @classmethod
    def _chain(cls, v):
        if v <= 0:
            raise ValueError("chain_id must be positive")
        return v

    @field_validator("token_decimals")
    @classmethod
    def _dec(cls, v):
        if v is not None and not (0 <= v <= 77):
            raise ValueError("token_decimals out of range")
        return v

    @field_validator("timestamp")
    @classmethod
    def _ts(cls, v: datetime):
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _consistency(self):
        if not self.event_id.startswith(f"{self.chain_id}:{self.transaction_hash}:"):
            raise ValueError("event_id must start with '<chain_id>:<transaction_hash>:'")
        if self.asset_type == "native":
            if self.token_contract is not None:
                raise ValueError("native asset cannot have token_contract")
            if self.asset_id != f"{self.chain_id}:native":
                raise ValueError("native asset_id must be '<chain_id>:native'")
        else:
            if self.token_contract is None:
                raise ValueError("erc20 asset requires token_contract")
            if self.asset_id != f"{self.chain_id}:erc20:{self.token_contract}":
                raise ValueError("erc20 asset_id must be '<chain_id>:erc20:<contract>'")
        if self.token_decimals is None:
            if self.normalized_value is not None:
                raise ValueError("normalized_value requires known token_decimals")
        else:
            expected = format_units(int(self.raw_value), self.token_decimals)
            if self.normalized_value is None:
                self.normalized_value = expected
            elif self.normalized_value != expected:
                raise ValueError("normalized_value inconsistent with raw_value/token_decimals")
        return self
