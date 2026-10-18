"""Raw provider rows (Etherscan field names) -> NormalizedTransfer.

Validation, normalization and duplicate handling live here, separate from retrieval
(providers.py) and graph construction (graph_engine). Provider-specific shapes stop here:
downstream code (Person 2/3) only ever sees NormalizedTransfer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from pydantic import ValidationError

from ...schemas.common import SourceRef
from ...schemas.transaction import NormalizedTransfer

Kind = Literal["native", "erc20"]


class NormalizationError(ValueError):
    pass


def make_event_id(chain_id: int, tx_hash: str, kind: Kind, log_index: int | None,
                  contract: str | None, frm: str, to: str, raw: str) -> str:
    base = f"{chain_id}:{tx_hash}:"
    if kind == "native":
        return base + "tx"
    if log_index is not None:
        return base + f"log-{log_index}"
    # Etherscan tokentx has no logIndex: derive a deterministic discriminator from the transfer itself.
    return base + f"tok-{contract[2:10]}-{frm[2:10]}-{to[2:10]}-{raw}"


def _req(row: dict, key: str) -> Any:
    if key not in row or row[key] in (None, ""):
        raise NormalizationError(f"missing field '{key}'")
    return row[key]


def _int(value: Any, name: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(str(value), 10)
    except ValueError:
        raise NormalizationError(f"field '{name}' is not an integer: {str(value)[:40]!r}")


def normalize_row(row: Any, kind: Kind, chain_id: int, source: SourceRef) -> NormalizedTransfer:
    if not isinstance(row, dict):
        raise NormalizationError("row is not an object")
    tx_hash = str(_req(row, "hash")).lower()
    frm = str(_req(row, "from")).lower()
    if row.get("to") in (None, ""):
        raise NormalizationError("missing 'to' (contract creation or malformed row)")
    to = str(row["to"]).lower()
    raw = str(_req(row, "value"))
    ts = datetime.fromtimestamp(_int(_req(row, "timeStamp"), "timeStamp"), tz=timezone.utc)
    block = _int(row["blockNumber"], "blockNumber") if row.get("blockNumber") not in (None, "") else None
    log_index = _int(row["logIndex"], "logIndex") if row.get("logIndex") not in (None, "") else None

    contract = decimals = symbol = None
    if kind == "native":
        asset_id, asset_type, event_type = f"{chain_id}:native", "native", "native_transfer"
        decimals = 18  # native-currency decimals for supported (EVM) chains
        symbol = "ETH" if chain_id == 1 else None
        if "isError" in row:
            status = "failed" if str(row["isError"]) == "1" else "success"
        elif "txreceipt_status" in row and row["txreceipt_status"] not in (None, ""):
            status = "success" if str(row["txreceipt_status"]) == "1" else "failed"
        else:
            status = "unknown"
    else:
        contract = str(_req(row, "contractAddress")).lower()
        asset_id, asset_type, event_type = f"{chain_id}:erc20:{contract}", "erc20", "erc20_transfer"
        if row.get("tokenDecimal") not in (None, ""):
            decimals = _int(row["tokenDecimal"], "tokenDecimal")  # unknown stays None; never guessed
        symbol = row.get("tokenSymbol") or None
        # An ERC-20 Transfer log only exists if the transaction did not revert.
        status = "success"

    try:
        return NormalizedTransfer(
            event_id=make_event_id(chain_id, tx_hash, kind, log_index, contract, frm, to, raw),
            chain_id=chain_id, transaction_hash=tx_hash, log_index=log_index, block_number=block,
            timestamp=ts, from_address=frm, to_address=to, asset_id=asset_id, asset_type=asset_type,
            asset_symbol=symbol, token_contract=contract, token_decimals=decimals, raw_value=raw,
            status=status, event_type=event_type, source=source,
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        raise NormalizationError(f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")


@dataclass
class NormalizationResult:
    events: list[NormalizedTransfer] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)   # {"index", "kind", "reason"}
    duplicates_dropped: int = 0
    conflicts: list[str] = field(default_factory=list)   # event_ids whose duplicates disagreed


def _canon(ev: NormalizedTransfer) -> str:
    d = ev.model_dump(mode="json", exclude={"source"})
    return json.dumps(d, sort_keys=True)


def deduplicate(events: Iterable[NormalizedTransfer]) -> tuple[list[NormalizedTransfer], int, list[str]]:
    """Deterministic duplicate handling.

    Same event_id + identical content -> keep one (provenance of the earliest retrieval).
    Same event_id + different content -> keep the lexicographically smallest canonical form and
    report the event_id as a conflict (never silently merged or summed).
    """
    groups: dict[str, list[NormalizedTransfer]] = {}
    for ev in events:
        groups.setdefault(ev.event_id, []).append(ev)
    out, dropped, conflicts = [], 0, []
    for eid in sorted(groups):
        g = sorted(groups[eid], key=lambda e: (_canon(e), e.source.retrieved_at, e.source.provider))
        dropped += len(g) - 1
        if len({_canon(e) for e in g}) > 1:
            conflicts.append(eid)
        out.append(g[0])
    out.sort(key=lambda e: (e.timestamp, e.event_id))
    return out, dropped, conflicts


def normalize_batch(native_rows: list, erc20_rows: list, chain_id: int, source: SourceRef) -> NormalizationResult:
    res = NormalizationResult()
    parsed: list[NormalizedTransfer] = []
    for kind, rows in (("native", native_rows), ("erc20", erc20_rows)):
        for i, row in enumerate(rows):
            try:
                parsed.append(normalize_row(row, kind, chain_id, source))
            except NormalizationError as exc:
                res.rejected.append({"index": i, "kind": kind, "reason": str(exc)})
    res.events, res.duplicates_dropped, res.conflicts = deduplicate(parsed)
    return res
