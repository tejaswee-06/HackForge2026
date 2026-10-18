"""Read-only blockchain data provider (Etherscan V2 indexed API).

Plain Ethereum JSON-RPC cannot return wallet history, so wallet queries use an indexed-data
API. Behaviour: env-configured key, request timeout, bounded retries with exponential backoff,
rate-limit handling, response-shape validation, TTL cache (cached results are labelled
'cached'), and NO silent fallback to synthetic data. The API key is never logged or returned.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, field_validator

from ...config import Settings
from ...schemas.transaction import normalize_address

log = logging.getLogger("astra.provider")


class ProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 provider: str = "etherscan", retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.provider, self.retry_after = provider, retry_after


class WalletQuery(BaseModel):
    chain_id: int
    address: str
    max_transfers: int = 100

    @field_validator("address")
    @classmethod
    def _a(cls, v):
        return normalize_address(v)

    @field_validator("max_transfers")
    @classmethod
    def _m(cls, v):
        if not 1 <= v <= 500:
            raise ValueError("max_transfers must be 1..500")
        return v


@dataclass
class ProviderResult:
    native_rows: list[dict]
    erc20_rows: list[dict]
    retrieved_at: datetime
    from_cache: bool
    provider: str
    reference: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EtherscanProvider:
    name = "etherscan"

    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.s, self._transport, self._sleep, self._clock = settings, transport, sleep, clock
        self._cache: dict[tuple, tuple[float, ProviderResult]] = {}
        self._lock = threading.Lock()
        self.last_error_code: Optional[str] = None
        self.last_success_at: Optional[datetime] = None
        self.request_count = 0  # HTTP attempts made (observability/tests)

    # ---- status (never includes the key) ----
    @property
    def configured(self) -> bool:
        return bool(self.s.etherscan_api_key)

    def health(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "status": "configured" if self.configured else "not_configured",
            "last_error_code": self.last_error_code,
            "last_success_at": self.last_success_at,
            "cache_entries": len(self._cache),
            "cache_ttl_s": self.s.cache_ttl_s,
        }

    # ---- public API ----
    def fetch_wallet_transfers(self, q: WalletQuery) -> ProviderResult:
        if not self.configured:
            raise ProviderError("PROVIDER_NOT_CONFIGURED",
                                "ETHERSCAN_API_KEY is not set; live wallet queries are unavailable. "
                                "Use a synthetic scenario for the offline demo.")
        if q.chain_id not in self.s.supported_chains:
            raise ProviderError("UNSUPPORTED_CHAIN", f"chain_id {q.chain_id} is not supported")
        key = (q.chain_id, q.address, q.max_transfers)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        base = {"chainid": q.chain_id, "module": "account", "address": q.address,
                "page": 1, "offset": q.max_transfers, "sort": "asc"}
        native = self._call({**base, "action": "txlist"})
        erc20 = self._call({**base, "action": "tokentx"})
        res = ProviderResult(native, erc20, _now(), False, self.name,
                             f"{self.s.etherscan_base_url} (account/txlist+tokentx, chainid={q.chain_id})")
        self._cache_put(key, res)
        self.last_success_at, self.last_error_code = res.retrieved_at, None
        return res

    # ---- cache ----
    def _cache_get(self, key):
        if self.s.cache_ttl_s <= 0:
            return None
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > self._clock():
                r = hit[1]
                return ProviderResult(r.native_rows, r.erc20_rows, r.retrieved_at, True, r.provider, r.reference)
            self._cache.pop(key, None)
        return None

    def _cache_put(self, key, res):
        if self.s.cache_ttl_s > 0:
            with self._lock:
                self._cache[key] = (self._clock() + self.s.cache_ttl_s, res)

    # ---- HTTP with bounded retries ----
    def _redact(self, text: str) -> str:
        k = self.s.etherscan_api_key
        return text.replace(k, "***") if k else text

    def _call(self, params: dict) -> list[dict]:
        attempt = 0
        while True:
            try:
                self.request_count += 1
                with httpx.Client(timeout=self.s.provider_timeout_s, transport=self._transport) as c:
                    resp = c.get(self.s.etherscan_base_url, params={**params, "apikey": self.s.etherscan_api_key})
                return self._parse(resp)
            except httpx.TimeoutException:
                err = ProviderError("PROVIDER_TIMEOUT", "provider request timed out", retryable=True)
            except httpx.TransportError as exc:
                err = ProviderError("PROVIDER_UNREACHABLE", f"provider unreachable ({type(exc).__name__})", retryable=True)
            except ProviderError as exc:
                err = exc
            self.last_error_code = err.code
            if not err.retryable:
                log.warning("provider error code=%s", err.code)
                raise err
            if attempt >= self.s.provider_max_retries:
                log.warning("provider retries exhausted last=%s attempts=%d", err.code, attempt + 1)
                self.last_error_code = "PROVIDER_RETRY_EXHAUSTED"
                raise ProviderError("PROVIDER_RETRY_EXHAUSTED",
                                    f"provider still failing after {attempt + 1} attempts (last: {err.code})",
                                    retryable=True)
            delay = self.s.provider_backoff_s * (2 ** attempt)
            if err.retry_after is not None:
                delay = max(delay, min(err.retry_after, 30.0))
            self._sleep(delay)
            attempt += 1

    def _parse(self, resp: httpx.Response) -> list[dict]:
        sc = resp.status_code
        if sc == 429:
            ra = resp.headers.get("Retry-After")
            raise ProviderError("PROVIDER_RATE_LIMITED", "provider rate limit hit", retryable=True,
                                retry_after=float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        if sc >= 500:
            raise ProviderError("PROVIDER_UNAVAILABLE", f"provider returned HTTP {sc}", retryable=True)
        if sc in (401, 403):
            raise ProviderError("PROVIDER_AUTH", f"provider rejected credentials (HTTP {sc})")
        if sc != 200:
            raise ProviderError("PROVIDER_BAD_RESPONSE", f"unexpected HTTP {sc} from provider")
        try:
            body = resp.json()
        except ValueError:
            raise ProviderError("PROVIDER_BAD_RESPONSE", "provider response is not valid JSON")
        if not isinstance(body, dict) or "status" not in body or "result" not in body:
            raise ProviderError("PROVIDER_BAD_RESPONSE", "provider response has unexpected shape")
        result, msg = body["result"], str(body.get("message", ""))
        if str(body["status"]) == "1":
            if not isinstance(result, list) or not all(isinstance(r, dict) for r in result):
                raise ProviderError("PROVIDER_BAD_RESPONSE", "provider 'result' is not a list of objects")
            return result
        text = f"{msg} {result if isinstance(result, str) else ''}".lower()
        if isinstance(result, list) and not result and ("no transactions" in text or "no records" in text):
            return []
        if "rate limit" in text:
            raise ProviderError("PROVIDER_RATE_LIMITED", "provider rate limit hit", retryable=True)
        if "api key" in text:
            raise ProviderError("PROVIDER_AUTH", "provider rejected the API key")
        raise ProviderError("PROVIDER_BAD_RESPONSE", self._redact(f"provider error: {msg}"[:160]))
