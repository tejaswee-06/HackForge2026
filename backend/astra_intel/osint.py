"""Evidence-first OSINT indicator extraction and validation helpers.

This module does not scrape websites or claim attribution. It extracts candidate
blockchain addresses/URLs from text supplied by a permitted source and preserves
provenance so a human can verify the source.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional
import re
from urllib.parse import urlparse

# Candidate patterns only. They are not chain validators and never establish ownership.
EVM_ADDRESS = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
BTC_BASE58 = re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b")
BTC_BECH32 = re.compile(r"\bbc1[ac-hj-np-z02-9]{11,71}\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_candidate_indicators(text: str, *, source_url: str, source_name: str,
                                 retrieved_at: str | None = None, chain_hint: str = "unknown") -> dict[str, Any]:
    """Extract candidate addresses and URLs from source text with provenance.

    source_url must be an absolute HTTP(S) URL. Source text must come from a
    permitted public source or user-supplied material.
    """
    if not isinstance(text, str): raise ValueError("text must be a string")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    if not source_name or not source_name.strip(): raise ValueError("source_name is required")
    retrieved = retrieved_at or datetime.now(timezone.utc).isoformat()
    indicators = []
    seen = set()
    for kind, pattern in (("evm_address_candidate", EVM_ADDRESS), ("bitcoin_address_candidate", BTC_BASE58), ("bitcoin_address_candidate", BTC_BECH32)):
        for match in pattern.finditer(text):
            value = match.group(0)
            key = (kind, value.lower() if kind == "evm_address_candidate" else value)
            if key in seen: continue
            seen.add(key)
            start, end = match.span()
            indicators.append({"indicator_id": f"I{len(indicators)+1:04d}", "kind": kind,
                "value": value, "chain_hint": chain_hint, "source_url": source_url,
                "source_name": source_name, "retrieved_at": retrieved,
                "source_quote": text[max(0,start-100):min(len(text),end+100)],
                "verification_status": "candidate_unverified",
                "claim_limit": "Mention in a source does not prove wallet ownership or wrongdoing."})
    urls = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,);]")
        if url != source_url and url not in urls: urls.append(url)
    return {"source": {"url": source_url, "name": source_name, "retrieved_at": retrieved,
                       "content_hash": None, "note": "Store a SHA-256 hash of the exact source bytes at ingestion for auditability."},
            "candidate_indicators": indicators, "referenced_urls": urls,
            "extraction_method": "regex_candidate_extraction_v1",
            "status": "extracted_candidates_require_human_verification"}


def validate_indicator_match(indicator: dict[str, Any], observed_entity: str) -> dict[str, Any]:
    """Return exact-string match information; no fuzzy attribution is inferred."""
    value = str(indicator.get("value", ""))
    observed = str(observed_entity or "")
    is_evm = str(indicator.get("kind", "")).startswith("evm_")
    matched = bool(value and observed and (value.lower() == observed.lower() if is_evm else value == observed))
    return {"indicator_id": indicator.get("indicator_id"), "observed_entity": observed,
            "exact_match": matched, "verification_status": "candidate_match_needs_source_review" if matched else "no_exact_match",
            "warning": "An exact textual match is a lead, not proof of ownership or culpability."}


# =============================================================================================
# v2 pipeline (integrated engine). The two functions above are kept unchanged for the stateless
# /api/intel endpoints and Person 2's original tests; the engine uses the functions below.
# =============================================================================================
import hashlib as _hashlib

from .keccak import checksum_status as _checksum_status

TX_HASH = re.compile(r"(?<![0-9a-fA-Fx])0x[a-fA-F0-9]{64}(?![0-9a-fA-F])")
EVM_ADDRESS_STRICT = re.compile(r"(?<![0-9a-fA-Fx])0x[a-fA-F0-9]{40}(?![0-9a-fA-F])")
# Shortened display forms such as 0x7a1b…91c0 or 0x7a1b...91c0 (UI/report style). Ambiguous by nature.
TRUNCATED_EVM = re.compile(r"(?<![0-9a-fA-Fx])0x([a-fA-F0-9]{4,12})(?:…|\.\.\.)([a-fA-F0-9]{3,12})(?![0-9a-fA-F])")

EXTRACTION_METHOD_V2 = "regex_indicator_extraction_v2+sentence_stance_v1"

# Sentence-level stance vocabulary. Deterministic and inspectable; documented in OSINT_PIPELINE.md.
INCIDENT_TERMS = ("exploit", "exploiter", "attacker", "hacker", "hack", "stolen", "drain", "drainer", "heist",
                  "laundering", "launder", "compromised", "incident", "phishing", "scam", "theft")
SERVICE_TERMS = ("exchange", "deposit address", "hot wallet", "cold wallet", "custodian", "custody",
                 "market maker", "payment processor", "otc desk", "service wallet", "payroll", "treasury",
                 "staking pool", "liquidity pool", "router contract")
BOUNDARY_TERMS = ("mixer", "tumbler", "privacy pool", "tornado", "coinjoin")
NEGATIONS = ("not ", "no longer ", "isn't ", "is not ", "never ", "unrelated to ", "no evidence ")


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max(text.rfind(c, 0, start) for c in ".!?\n")
    rights = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    return left + 1, (min(rights) + 1 if rights else len(text))


def _terms(sentence: str, vocab: tuple[str, ...]) -> list[str]:
    s = sentence.lower()
    hits = []
    for t in vocab:
        i = s.find(t)
        while i != -1:
            window = s[max(0, i - 18):i]
            if not any(n in window for n in NEGATIONS):
                hits.append(t)
                break
            i = s.find(t, i + 1)
    return hits


def classify_stance(sentence: str) -> tuple[str, list[str]]:
    """incident_association | service_label | privacy_boundary | mixed | neutral (+ matched terms).

    A stance is what the SOURCE asserts in the sentence that mentions the indicator. It is a
    source-reported claim, never an ASTRA conclusion. Simple negation ("not an exchange") within a
    short window before the term suppresses it; anything more subtle is a documented limitation.
    """
    inc, svc, bnd = _terms(sentence, INCIDENT_TERMS), _terms(sentence, SERVICE_TERMS), _terms(sentence, BOUNDARY_TERMS)
    if bnd:
        return "privacy_boundary", bnd
    if inc and svc:
        return "mixed", inc + svc
    if inc:
        return "incident_association", inc
    if svc:
        return "service_label", svc
    return "neutral", []


def content_hash(text: str) -> str:
    return "sha256:" + _hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_indicators(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and validate indicators from one collected document (dict with doc_id, text, ...).

    Indicator types: evm_address, tx_hash, truncated_evm_address, bitcoin_address_candidate.
    Every indicator keeps: document id, source URL/name, publication + retrieval time, the exact
    character span and an excerpt, the extraction method, a validation status and the stance of
    the sentence it appears in. Overlapping matches are resolved longest-first (a 64-hex tx hash
    is never also reported as a 40-hex address).
    """
    text = doc.get("text") or ""
    out: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(kind: str, m: re.Match, normalized: str, validation: str, extra: Optional[dict] = None):
        a, b = m.span()
        if not free(a, b):
            return
        taken.append((a, b))
        s0, s1 = _sentence_bounds(text, a, b)
        sentence = text[s0:s1].strip()
        stance, terms = classify_stance(sentence)
        out.append({
            "indicator_id": f"{doc['doc_id']}:i{len(out) + 1:03d}", "type": kind,
            "raw_value": m.group(0), "normalized_value": normalized, "span": [a, b],
            "excerpt": text[max(0, a - 120):min(len(text), b + 120)], "sentence": sentence[:600],
            "validation_status": validation, "stance": stance, "stance_terms": terms,
            "doc_id": doc["doc_id"], "source_name": doc.get("source_name"), "source_url": doc.get("url"),
            "published_at": doc.get("published_at"), "retrieved_at": doc.get("retrieved_at"),
            "extraction_method": EXTRACTION_METHOD_V2, **(extra or {}),
            "claim_limit": "A mention in a source does not prove wallet ownership, control or wrongdoing.",
        })

    for m in TX_HASH.finditer(text):
        add("tx_hash", m, m.group(0).lower(), "format_valid")
    for m in EVM_ADDRESS_STRICT.finditer(text):
        cs = _checksum_status(m.group(0))
        add("evm_address", m, m.group(0).lower(),
            {"checksum_valid": "checksum_valid", "checksum_invalid": "checksum_invalid",
             "no_checksum": "format_valid_no_checksum"}[cs])
    for m in TRUNCATED_EVM.finditer(text):
        add("truncated_evm_address", m, f"0x{m.group(1).lower()}…{m.group(2).lower()}", "pattern_only",
            {"prefix": "0x" + m.group(1).lower(), "suffix": m.group(2).lower()})
    for pat in (BTC_BASE58, BTC_BECH32):
        for m in pat.finditer(text):
            add("bitcoin_address_candidate", m, m.group(0), "pattern_only_unsupported_chain")
    out.sort(key=lambda x: x["span"][0])
    for i, ind in enumerate(out, 1):
        ind["indicator_id"] = f"{doc['doc_id']}:i{i:03d}"
    return out
