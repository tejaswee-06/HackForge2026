"""Glue between Person 1's investigation records and Person 2's engine (one code path for everything).

Leads, evidence, hypotheses, sensitivity, replay, reports and the frontend bundle ALL call
``AnalysisService.run`` -> ``astra_intel.engine.analyze``. Results are cached by a key that includes
the OSINT collection version, configuration hash, cutoff and overrides, so a changed input always
recomputes and an unchanged input is reproducible.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from astra_intel.engine import AnalysisConfig, Overrides, analyze

from ..schemas.intel import AnalysisConfigModel
from ..store import InvestigationRecord


def to_config(m: Optional[AnalysisConfigModel]) -> AnalysisConfig:
    return AnalysisConfig(**(m.model_dump() if m else {})).validate()


def dataset_ref(rec: InvestigationRecord) -> str:
    s = rec.ingestion.sources[0] if rec.ingestion.sources else None
    return f"{s.provider}:{s.reference}@{s.retrieved_at.isoformat()}" if s else rec.id


class AnalysisService:
    def __init__(self, max_entries: int = 32):
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.runs = 0  # engine executions (observability/tests: proves recomputation)

    def run(self, rec: InvestigationRecord, cfg: Optional[AnalysisConfig] = None, *, cutoff: Optional[datetime] = None,
            overrides: Optional[Overrides] = None) -> dict:
        cfg = cfg or AnalysisConfig()
        ov = overrides or Overrides()
        key = json.dumps([rec.id, rec.osint.version, cfg.hash(), cutoff.isoformat() if cutoff else None,
                          sorted(ov.remove_evidence_ids), sorted(ov.downgrade_evidence_ids), ov.downgrade_factor,
                          sorted(ov.remove_doc_ids)])
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        res = analyze(investigation_id=rec.id, chain_id=rec.chain_id, start_address=rec.start_address,
                      events=rec.ingestion.events, documents=rec.osint.engine_documents(), data_mode=rec.data_mode,
                      dataset_ref=dataset_ref(rec), config=cfg, cutoff=cutoff, overrides=ov,
                      source_status=rec.osint.source_status(), duplicates_dropped=rec.ingestion.duplicates_dropped,
                      extra_limitations=rec.limitations)
        with self._lock:
            self.runs += 1
            self._cache[key] = res
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return res
