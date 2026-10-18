#!/usr/bin/env python3
"""Deterministically regenerate data/osint/ghost_attacks_synthetic_v1.json.

ALL DOCUMENTS ARE SYNTHETIC. URLs use the reserved ``.invalid`` TLD (RFC 2606) so they can never
resolve to a real publisher. The documents reference the synthetic addresses / transaction hashes of
data/scenarios/ghost_attacks_synthetic_v1.json and deliberately cover the OSINT edge cases:

  A  incident report (exact matches)              E  stale, pre-incident forum post
  B  repository issue (tx hash, shortened forms,  F  undated analyst note (privacy boundary)
     an upper-case address = no EIP-55 checksum)  G  feed outage (collection failed)
  C  label registry (service label -> contradiction with A for X5)
  D  mirror of A (identical text -> same independence group via Union-Find)
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

T0 = datetime(2026, 10, 1, 2, 14, tzinfo=timezone.utc)
A = lambda n: "0x" + "0" * 36 + f"{n:04x}"
X = {i: A(0xA000 + i) for i in range(8)}
H = lambda label: "0x" + hashlib.sha256(f"astra-synthetic-v1:{label}".encode()).hexdigest()
iso = lambda d: d.isoformat().replace("+00:00", "Z")
BASE = "https://synthetic.astra.invalid/ghost-attacks"

# X addresses contain a single letter, so EIP-55 mixed case is impossible: an all-upper-case form
# is reported as 'format_valid_no_checksum' (checksum failures are covered by unit tests).
upper = "0x" + X[2][2:].upper()

incident_text = (
    "SYNTHETIC INCIDENT REPORT (test data). Our monitoring observed a protocol exploit at 02:14 UTC on 1 Oct 2026. "
    f"The attacker wallet {X[0]} drained the lending pool. "
    f"Stolen funds were forwarded to {X[1]} within minutes. "
    f"A second hop sent part of the stolen funds to {X[5]}. "
    f"Funds later reached {X[6]}, which we consider part of the exploit flow. "
    "We have not attributed the activity to any person or organisation.")

docs = [
    {"source_id": "syn-threat-report", "source_name": "Synthetic Threat Intel Blog", "source_type": "threat_report",
     "publisher_group": "synthetic-threat-intel", "url": f"{BASE}/reports/incident-0142", "title": "Lending pool exploit - initial findings",
     "published_at": iso(T0 + timedelta(minutes=45)), "retrieved_at": iso(T0 + timedelta(minutes=50)),
     "reliability": 0.8, "reliability_rationale": "Rubric: named security publisher, first-hand monitoring claim, no attribution.",
     "text": incident_text},
    {"source_id": "syn-repo", "source_name": "Synthetic GitHub issue", "source_type": "repository",
     "publisher_group": "synthetic-repo-maintainers", "url": f"{BASE}/repo/issues/77", "title": "Tracking: exploit transactions",
     "published_at": iso(T0 + timedelta(hours=2)), "retrieved_at": iso(T0 + timedelta(hours=2, minutes=5)),
     "reliability": 0.5, "reliability_rationale": "Rubric: community issue tracker; unverified contributor claims.",
     "text": (f"Tracking issue for the exploit. Transaction {H('e7')} moved stolen funds onward. "
              "One recipient looks like 0x0000…a003 in the explorer screenshot. "
              "Someone else posted 0x0000…001 but that shortened form is unclear. "
              f"Contributor pasted {upper} as another relay of the exploit.")},
    {"source_id": "syn-label-registry", "source_name": "Synthetic Address Label Registry", "source_type": "label_registry",
     "publisher_group": "synthetic-labels", "url": f"{BASE}/labels/a005", "title": "Label: exchange deposit cluster",
     "published_at": iso(T0 - timedelta(days=30)), "retrieved_at": iso(T0 + timedelta(hours=3)),
     "reliability": 0.7, "reliability_rationale": "Rubric: curated public label set; labels can be outdated.",
     "text": f"Address {X[5]} is labelled as a centralized exchange deposit address (hot wallet cluster)."},
    {"source_id": "syn-mirror", "source_name": "Synthetic News Mirror", "source_type": "threat_report",
     "publisher_group": "synthetic-news-mirror", "url": f"{BASE}/mirror/incident-0142", "title": "Lending pool exploit (republished)",
     "published_at": iso(T0 + timedelta(hours=3)), "retrieved_at": iso(T0 + timedelta(hours=3, minutes=10)),
     "reliability": 0.6, "reliability_rationale": "Rubric: aggregator republishing another publisher's text.",
     "text": incident_text},
    {"source_id": "syn-forum", "source_name": "Synthetic Community Forum", "source_type": "forum",
     "publisher_group": "synthetic-forum", "url": f"{BASE}/forum/thread/9", "title": "Phishing report (2025)",
     "published_at": "2025-06-01T09:00:00Z", "retrieved_at": iso(T0 + timedelta(hours=4)),
     "reliability": 0.3, "reliability_rationale": "Rubric: anonymous forum post; old.",
     "text": f"Warning: {X[4]} was used in a phishing scam last year."},
    {"source_id": "syn-analyst", "source_name": "Synthetic Analyst Note", "source_type": "analyst_note",
     "publisher_group": "synthetic-analyst", "url": f"{BASE}/notes/analyst-3", "title": "Possible mixer deposit",
     "published_at": None, "retrieved_at": iso(T0 + timedelta(hours=4, minutes=30)),
     "reliability": 0.5, "reliability_rationale": "Rubric: internal analyst observation; publication time not recorded.",
     "text": f"Possible mixer deposit: {X[7]} sent funds into a privacy pool. Exit side not observed."},
    {"source_id": "syn-feed-outage", "source_name": "Synthetic Threat Feed", "source_type": "threat_feed",
     "publisher_group": "synthetic-feed", "url": f"{BASE}/feed/latest", "title": None, "published_at": None,
     "retrieved_at": iso(T0 + timedelta(hours=1)), "reliability": 0.6,
     "reliability_rationale": "Rubric: automated feed.", "text": "", "collection_status": "failed",
     "error": "HTTP 503 from feed (simulated provider outage)"},
]
doc = {"scenario_id": "ghost_attacks_synthetic_v1", "dataset_version": "1.0.0", "data_mode": "synthetic", "synthetic": True,
       "notice": "ALL DOCUMENTS ARE SYNTHETIC TEST DATA on a reserved .invalid domain; they describe no real event or person.",
       "documents": docs}
out = ROOT / "data" / "osint" / "ghost_attacks_synthetic_v1.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(doc, indent=2) + "\n")
print("wrote", out)
