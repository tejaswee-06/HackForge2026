"""OSINT source connectivity and ingestion (Person 1 ownership).

Responsibilities here: retrieving / loading documents from permitted sources, preserving provenance
(source, URL, publication + retrieval time, content hash, extraction method, reliability rubric,
data mode, collection status/errors) and keeping a per-investigation source registry.
NO analysis happens here: indicator extraction, matching and evidence assessment are Person 2's
engine (``astra_intel``). Documents are never fabricated to hide a failed collection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from ...schemas.intel import AnalystDocumentCreate, SourceDocument

log = logging.getLogger("astra.osint")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_doc_id(source_id: str, url: str, chash: str) -> str:
    return "doc-" + hashlib.sha256(f"{source_id}|{url}|{chash}".encode()).hexdigest()[:12]


def build_document(raw: dict, *, data_mode: str, extraction_method: str) -> SourceDocument:
    text = raw.get("text") or ""
    ch = content_hash(text)
    return SourceDocument(
        doc_id=make_doc_id(raw["source_id"], raw["url"], ch), source_id=raw["source_id"],
        source_name=raw["source_name"], source_type=raw.get("source_type", "other"),
        publisher_group=raw.get("publisher_group"), url=raw["url"], title=raw.get("title"),
        published_at=raw.get("published_at"), retrieved_at=raw["retrieved_at"], text=text, content_hash=ch,
        extraction_method=extraction_method, reliability=raw.get("reliability", 0.5),
        reliability_rationale=raw.get("reliability_rationale", "not stated"), data_mode=data_mode,
        collection_status=raw.get("collection_status", "collected"), error=raw.get("error"),
        terms_note=raw.get("terms_note"))


@dataclass
class OSINTCollection:
    """Per-investigation document store + source registry. Thread-safe; versioned for cache keys."""

    docs: dict[str, SourceDocument] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, doc: SourceDocument) -> bool:
        with self._lock:
            new = doc.doc_id not in self.docs
            self.docs[doc.doc_id] = doc
            s = self.sources.setdefault(doc.source_id, {
                "source_id": doc.source_id, "name": doc.source_name, "type": doc.source_type,
                "data_mode": doc.data_mode, "documents": 0, "failed": 0, "status": "ok",
                "last_retrieved_at": None, "error": None, "url": doc.url, "terms_note": doc.terms_note})
            if new:
                s["documents"] += 1 if doc.collection_status == "collected" else 0
                s["failed"] += 1 if doc.collection_status != "collected" else 0
            if doc.collection_status != "collected":
                s["status"], s["error"] = "failed", doc.error
            last = s["last_retrieved_at"]
            s["last_retrieved_at"] = max(last, doc.retrieved_at) if last else doc.retrieved_at
            self.version += 1
            return new

    def set_source_status(self, source_id: str, **kw) -> None:
        with self._lock:
            self.sources.setdefault(source_id, {"source_id": source_id, "documents": 0, "failed": 0})
            self.sources[source_id].update(kw)
            self.version += 1

    def engine_documents(self) -> list[dict]:
        with self._lock:
            return [d.model_dump() for d in sorted(self.docs.values(), key=lambda d: d.doc_id)]

    def source_status(self) -> list[dict]:
        with self._lock:
            return [dict(v) for _, v in sorted(self.sources.items())]


def load_fixture_documents(data_dir: Path, scenario_id: str) -> Optional[OSINTCollection]:
    p = data_dir / "osint" / f"{scenario_id}.json"
    if not p.exists():
        return None
    body = json.loads(p.read_text())
    if body.get("synthetic") and body.get("data_mode") != "synthetic":
        raise ValueError(f"{p.name}: synthetic OSINT fixture must have data_mode 'synthetic'")
    col = OSINTCollection()
    for raw in body["documents"]:
        col.add(build_document(raw, data_mode=body.get("data_mode", "synthetic"), extraction_method="fixture_text_v1"))
    return col


def analyst_document(body: AnalystDocumentCreate, data_mode: str) -> SourceDocument:
    """Analyst pasted text from a public page they opened. data_mode follows the investigation
    (an analyst note attached to a synthetic case stays synthetic-labelled)."""
    raw = body.model_dump()
    raw.update(source_id="analyst-supplied", retrieved_at=_now(),
               terms_note="Supplied by the analyst; ASTRA did not fetch this URL.")
    return build_document(raw, data_mode=data_mode, extraction_method="analyst_supplied_text_v1")


# --------------------------------------------------------------------------------------------
# Live adapter: GitHub REST issue search (permitted public API; rate limits respected)
# --------------------------------------------------------------------------------------------
class OSINTSourceError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, retry_after: Optional[float] = None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.retry_after = code, message, retryable, retry_after


class GitHubIssuesSource:
    """Searches public GitHub issues/PRs for an exact indicator string (e.g. an address).

    * Disabled unless ASTRA_OSINT_GITHUB_ENABLED=1 (no surprise network calls).
    * Optional GITHUB_TOKEN (server-side only) raises the search rate limit; never logged.
    * Timeout, bounded exponential-backoff retries, 403/429 rate-limit handling with
      Retry-After / X-RateLimit-Reset, response-shape validation, max items per query.
    """

    source_id = "github-issues-search"
    name = "GitHub REST issue search"
    url = "https://api.github.com/search/issues"
    terms = ("GitHub REST API search (https://docs.github.com/en/rest/search/search); public data only; "
             "unauthenticated search is limited to ~10 requests/minute.")

    def __init__(self, *, enabled: bool, token: Optional[str] = None, timeout_s: float = 10.0, max_retries: int = 2,
                 backoff_s: float = 0.5, transport: Optional[httpx.BaseTransport] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.enabled, self._token, self.timeout_s = enabled, token, timeout_s
        self.max_retries, self.backoff_s, self._transport, self._sleep = max_retries, backoff_s, transport, sleep
        self.last_error: Optional[str] = None
        self.request_count = 0

    def health(self) -> dict:
        return {"enabled": self.enabled, "authenticated": bool(self._token), "last_error_code": self.last_error,
                "status": "disabled" if not self.enabled else ("degraded" if self.last_error else "ok")}

    def search(self, term: str, *, max_items: int = 10) -> list[dict]:
        if not self.enabled:
            raise OSINTSourceError("OSINT_SOURCE_DISABLED", "GitHub source is disabled (set ASTRA_OSINT_GITHUB_ENABLED=1).")
        if not term or len(term) > 200 or any(c in term for c in '\n\r"'):
            raise OSINTSourceError("OSINT_BAD_TERM", "search term must be a short single-line string")
        params = {"q": f'"{term}"', "per_page": max(1, min(max_items, 30)), "sort": "created", "order": "asc"}
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "ASTRA-investigation-prototype"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        attempt = 0
        while True:
            try:
                self.request_count += 1
                with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                    r = c.get(self.url, params=params, headers=headers)
                items = self._parse(r)
                self.last_error = None
                break
            except httpx.TimeoutException:
                err = OSINTSourceError("OSINT_TIMEOUT", "GitHub request timed out", retryable=True)
            except httpx.TransportError as exc:
                err = OSINTSourceError("OSINT_UNREACHABLE", f"GitHub unreachable ({type(exc).__name__})", retryable=True)
            except OSINTSourceError as exc:
                err = exc
            self.last_error = err.code
            if not err.retryable or attempt >= self.max_retries:
                raise err
            delay = self.backoff_s * (2 ** attempt)
            if err.retry_after is not None:
                if err.retry_after > 30:
                    raise err  # do not block a request for minutes; report rate limit instead
                delay = max(delay, err.retry_after)
            self._sleep(delay)
            attempt += 1
        retrieved = _now()
        out = []
        for it in items[:max_items]:
            text = f"{it.get('title') or ''}\n\n{(it.get('body') or '')[:20000]}"
            out.append({"source_id": self.source_id, "source_name": self.name, "source_type": "repository",
                        "publisher_group": it.get("repository_url"), "url": it["html_url"], "title": it.get("title"),
                        "published_at": it.get("created_at"), "retrieved_at": retrieved, "text": text,
                        "reliability": 0.4, "reliability_rationale":
                            "Rubric: public issue/PR text written by arbitrary contributors; unverified claims.",
                        "terms_note": self.terms})
        return out

    @staticmethod
    def _parse(r: httpx.Response) -> list[dict]:
        if r.status_code in (403, 429):
            remaining = r.headers.get("X-RateLimit-Remaining")
            if r.status_code == 429 or remaining == "0" or "rate limit" in r.text.lower():
                ra = r.headers.get("Retry-After")
                reset = r.headers.get("X-RateLimit-Reset")
                wait = float(ra) if ra and ra.isdigit() else (max(0.0, float(reset) - time.time()) if reset and reset.isdigit() else None)
                raise OSINTSourceError("OSINT_RATE_LIMITED", "GitHub rate limit reached", retryable=True, retry_after=wait)
            raise OSINTSourceError("OSINT_FORBIDDEN", f"GitHub returned HTTP {r.status_code}")
        if r.status_code >= 500:
            raise OSINTSourceError("OSINT_UNAVAILABLE", f"GitHub returned HTTP {r.status_code}", retryable=True)
        if r.status_code != 200:
            raise OSINTSourceError("OSINT_BAD_RESPONSE", f"GitHub returned HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise OSINTSourceError("OSINT_BAD_RESPONSE", "GitHub response is not JSON")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("html_url"), str) for i in items):
            raise OSINTSourceError("OSINT_BAD_RESPONSE", "GitHub response has unexpected shape")
        return items
