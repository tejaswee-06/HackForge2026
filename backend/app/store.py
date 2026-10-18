"""In-memory investigation registry (MVP). Restart => demo cases are re-created deterministically;
live wallet investigations must be re-created (documented limitation)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .schemas.common import DataMode, SourceRef
from .services.graph_engine.engine import TransferGraph
from .services.ingestion.service import IngestionResult
from .services.osint_ingest.service import OSINTCollection


@dataclass
class InvestigationRecord:
    id: str
    chain_id: int
    scenario_id: Optional[str]
    start_address: Optional[str]
    scope: dict
    data_mode: DataMode
    ingestion: IngestionResult
    graph: TransferGraph
    created_at: datetime
    limitations: list[str] = field(default_factory=list)
    osint: OSINTCollection = field(default_factory=OSINTCollection)
    simulation: Optional[dict] = None          # generator metadata (ground truth is NOT stored here)

    @property
    def sources(self) -> list[SourceRef]:
        return self.ingestion.sources

    @property
    def data_cutoff(self) -> Optional[datetime]:
        evs = self.ingestion.events
        return max((e.timestamp for e in evs), default=None)


class InvestigationStore:
    def __init__(self):
        self._d: dict[str, InvestigationRecord] = {}
        self._lock = threading.Lock()

    def put(self, rec: InvestigationRecord) -> None:
        with self._lock:
            self._d[rec.id] = rec

    def get(self, inv_id: str) -> Optional[InvestigationRecord]:
        with self._lock:
            return self._d.get(inv_id)

    def list(self) -> list[InvestigationRecord]:
        with self._lock:
            return sorted(self._d.values(), key=lambda r: r.id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)
