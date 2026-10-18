#!/usr/bin/env python3
"""Deterministically regenerate data/scenarios/ghost_attacks_synthetic_v1.json.

EVERYTHING in the output is SYNTHETIC: addresses are low-entropy placeholders, transaction hashes
are sha256("astra-synthetic-v1:<label>"). Expected paths/amounts below are written BY HAND from the
topology (not computed by the graph engine), so tests compare the engine to an independent oracle.

Topology (all native ETH unless noted; t = minutes after 2026-10-01T02:14:00Z):
  e1  X0->X1 100  t+0     e2  X0->X2 60   t+1     f1  X0->X7 5 (FAILED tx, excluded)
  e3  X1->X3 99   t+5 (listed twice -> duplicate)  e5  X2->X4 30 t+6   e6 X2->X5 29 t+7
  e4  X3->X5 98   t+9     e7  X5->X6 120  t+15    e8  X6->X3 5  t+20   (cycle X3->X5->X6->X3)
  e9  Y1->Y2 7    t+3     (disconnected from the incident)
  t1  X2->X4 1.000000 TOK (6 dec) t+8 log 12      t2  X4->X7 0.400000 TOK t+12 log 3  (unlike asset)
"""
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

T0 = datetime(2026, 10, 1, 2, 14, tzinfo=timezone.utc)
ETH = 10**18
TOKEN = "0x" + "0" * 36 + "f001"
A = lambda n: "0x" + "0" * 36 + f"{n:04x}"
X = {i: A(0xA000 + i) for i in range(8)}
Y = {1: A(0xB001), 2: A(0xB002)}
H = lambda label: "0x" + hashlib.sha256(f"astra-synthetic-v1:{label}".encode()).hexdigest()
ts = lambda m: str(int(T0.timestamp()) + m * 60)

def native(label, frm, to, eth, minute, block, err="0"):
    return {"hash": H(label), "blockNumber": str(18_000_000 + block), "timeStamp": ts(minute), "from": frm, "to": to,
            "value": str(eth * ETH), "isError": err, "txreceipt_status": "0" if err == "1" else "1", "astraSynthetic": True}

def token(label, frm, to, raw, minute, block, log):
    return {"hash": H(label), "blockNumber": str(18_000_000 + block), "timeStamp": ts(minute), "from": frm, "to": to,
            "value": str(raw), "contractAddress": TOKEN, "tokenDecimal": "6", "tokenSymbol": "SYNTH",
            "logIndex": str(log), "astraSynthetic": True}

e = {
    "e1": native("e1", X[0], X[1], 100, 0, 1), "e2": native("e2", X[0], X[2], 60, 1, 1),
    "f1": native("f1", X[0], X[7], 5, 2, 1, err="1"), "e9": native("e9", Y[1], Y[2], 7, 3, 2),
    "e3": native("e3", X[1], X[3], 99, 5, 3), "e5": native("e5", X[2], X[4], 30, 6, 4),
    "e6": native("e6", X[2], X[5], 29, 7, 4), "e4": native("e4", X[3], X[5], 98, 9, 5),
    "e7": native("e7", X[5], X[6], 120, 15, 7), "e8": native("e8", X[6], X[3], 5, 20, 9),
}
native_rows = [e[k] for k in ("e1", "e2", "f1", "e9", "e3", "e3", "e5", "e6", "e4", "e7", "e8")]  # e3 twice
tok = {"t1": token("t1", X[2], X[4], 1_000_000, 8, 5, 12), "t2": token("t2", X[4], X[7], 400_000, 12, 6, 3)}
erc20_rows = [tok["t1"], tok["t2"]]

eid = lambda row, kind="tx": f"1:{row['hash']}:{kind}"
nid = lambda a: f"1:{a}"
def path(addrs, rows, bottleneck, reason, asset, kind="tx", norm=None):
    return {"asset_id": asset, "node_ids": [nid(a) for a in addrs],
            "event_ids": [eid(r, kind) if kind == "tx" else f"1:{r['hash']}:log-{r['logIndex']}" for r in rows],
            "bottleneck_raw": str(bottleneck), "bottleneck_normalized": norm, "terminated_reason": reason}

NAT = "1:native"; TOK = f"1:erc20:{TOKEN}"
expected = {
    "native_asset_id": NAT, "token_asset_id": TOK, "token_contract": TOKEN,
    "counts": {"raw_native_rows": 11, "raw_erc20_rows": 2, "unique_events": 12, "duplicates_dropped": 1,
               "graph_edges": 11, "graph_nodes": 10, "weakly_connected_components": 2},
    "excluded_failed_event_ids": [eid(e["f1"])],
    "disconnected_addresses": [Y[1], Y[2]],
    "cycle": [X[3], X[5], X[6], X[3]],
    "paths_native_from_start_depth6": [
        path([X[0], X[1], X[3], X[5], X[6]], [e["e1"], e["e3"], e["e4"], e["e7"]], 98 * ETH, "cycle_blocked", NAT, norm="98.000000000000000000"),
        path([X[0], X[2], X[5], X[6], X[3]], [e["e2"], e["e6"], e["e7"], e["e8"]], 5 * ETH, "leaf", NAT, norm="5.000000000000000000"),
        path([X[0], X[2], X[4]], [e["e2"], e["e5"]], 30 * ETH, "leaf", NAT, norm="30.000000000000000000"),
    ],
    "paths_native_from_start_depth2": [
        path([X[0], X[1], X[3]], [e["e1"], e["e3"]], 99 * ETH, "max_depth", NAT, norm="99.000000000000000000"),
        path([X[0], X[2], X[5]], [e["e2"], e["e6"]], 29 * ETH, "max_depth", NAT, norm="29.000000000000000000"),
        path([X[0], X[2], X[4]], [e["e2"], e["e5"]], 30 * ETH, "leaf", NAT, norm="30.000000000000000000"),
    ],
    "paths_token_from_X2": [
        path([X[2], X[4], X[7]], [tok["t1"], tok["t2"]], 400_000, "leaf", TOK, kind="log", norm="0.400000"),
    ],
    "notes": "Unlike assets are never chained: X0 has no token edges, so X0 yields no token paths. "
             "X0->X2->X5->X6->X3 stops at X3 because X3->X5 (t+9) precedes the arrival (t+20): time-ordered.",
}

doc = {
    "scenario_id": "ghost_attacks_synthetic_v1",
    "name": "Ghost Attacks - simulated $42M exploit flow (SYNTHETIC)",
    "description": "Small deterministic simulated incident: a split, a multi-hop path, a cycle, a duplicate record, a failed "
                   "transaction, an unlike-token transfer and a disconnected pair. Amounts are scaled-down placeholders.",
    "dataset_version": "1.0.0", "data_mode": "synthetic", "synthetic": True,
    "synthetic_notice": "ALL RECORDS IN THIS SCENARIO ARE SYNTHETIC TEST DATA. Addresses, hashes, amounts and timestamps are "
                        "invented and do not correspond to any real blockchain activity or real person.",
    "chain_id": 1, "fixture_retrieved_at": "2026-10-01T00:00:00+00:00", "start_address": X[0],
    "native_rows": native_rows, "erc20_rows": erc20_rows, "expected": expected,
    "limitations": ["Simulated incident; results demonstrate the pipeline, not real-world attribution.",
                    "Graph connectivity and timing are observed relationships, not proof of common ownership."],
}
out = Path(__file__).resolve().parents[1] / "data" / "scenarios" / "ghost_attacks_synthetic_v1.json"
out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print("wrote", out)
