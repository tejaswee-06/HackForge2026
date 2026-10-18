"""Deterministic synthetic investigation generator (Person 2: labelled scenarios).

Produces RAW provider-shaped rows (Etherscan field names) and raw OSINT documents. The backend runs
them through the SAME Person 1 normalizer and the SAME Person 2 engine as live data. Nothing here
computes a score, a ranking, a hypothesis or a report; ground-truth labels are returned separately
and are only consumed by the evaluation harness.

Scenario kinds
  incident      exploit origin -> multi-hop splitting tree with rapid forwarding; one branch deposits
                into an exchange-like hub (a benign service that RECEIVES incident value); OSINT incident
                report + service label + contradiction + stale/undated/ambiguous/duplicate docs + outage
  benign        an exchange-like payout wallet paying many users slowly over the window; service labels
                from two independent sources; no incident report
  insufficient  a handful of slow transfers and no discriminating public-source evidence
All addresses/hashes are sha256-derived placeholders; URLs use the reserved .invalid TLD.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

ETH = 10 ** 18
TOKEN_CONTRACT_LABEL = "synthetic-token"
MAX_TREE_NODES = 400


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:10]


def generate(cfg: dict[str, Any]) -> dict[str, Any]:
    seed, kind = int(cfg["seed"]), cfg["scenario_kind"]
    rng = random.Random(f"astra-sim|{seed}|{kind}")
    salt = f"{seed}:{kind}"
    addr = lambda label: "0x" + hashlib.sha256(f"astra-sim:{salt}:{label}".encode()).hexdigest()[:40]
    txh = lambda label: "0x" + hashlib.sha256(f"astra-sim-tx:{salt}:{label}".encode()).hexdigest()
    st = cfg["start_time"]
    t0 = int((st if isinstance(st, datetime) else datetime.fromisoformat(str(st).replace("Z", "+00:00"))).timestamp())
    window = int(float(cfg["window_hours"]) * 3600)
    token = addr(TOKEN_CONTRACT_LABEL)
    native: list[dict] = []
    erc20: list[dict] = []
    counter = [0]
    truth = {"relevant": set(), "service": set(), "benign": set(), "noise": set()}

    def tx(frm, to, wei, ts, label=None, err=False):
        counter[0] += 1
        row = {"hash": txh(label or f"n{counter[0]}"), "blockNumber": str(19_000_000 + max(0, ts - t0) // 12),
               "timeStamp": str(ts), "from": frm, "to": to, "value": str(int(wei)), "isError": "1" if err else "0",
               "txreceipt_status": "0" if err else "1", "astraSynthetic": True}
        native.append(row)
        return row

    def tok(frm, to, raw, ts):
        counter[0] += 1
        row = {"hash": txh(f"t{counter[0]}"), "blockNumber": str(19_000_000 + max(0, ts - t0) // 12), "timeStamp": str(ts),
               "from": frm, "to": to, "value": str(int(raw)), "contractAddress": token, "tokenDecimal": "6",
               "tokenSymbol": "SYNUSD", "logIndex": str(counter[0] % 200), "astraSynthetic": True}
        erc20.append(row)
        return row

    origin = addr("origin")
    tree_nodes: list[tuple[str, int, int]] = []  # (address, depth, arrival ts)
    tree_edges: list[dict] = []
    service = addr("service-hub")
    mention: dict[str, str] = {}

    if kind == "incident":
        amount = cfg["base_amount_eth"] * ETH
        frontier = [(origin, amount, t0, 0)]
        n_nodes = 0
        while frontier:
            nxt = []
            for (a, amt, t_arr, d) in frontier:
                if d >= cfg["depth"] or amt <= 0:
                    continue
                k = rng.randint(max(1, cfg["branching"] - 1), cfg["branching"])
                if n_nodes + k > MAX_TREE_NODES:
                    k = 1 if n_nodes < MAX_TREE_NODES else 0
                if k == 0:
                    continue
                keep = rng.uniform(0.01, 0.05)
                weights = [rng.uniform(0.5, 1.5) for _ in range(k)]
                tot = sum(weights)
                t = t_arr
                for i in range(k):
                    t += rng.randint(20, 420)  # rapid forwarding: 20 s - 7 min after receipt
                    child = addr(f"hop{d + 1}-{len(tree_nodes)}")
                    part = int(amt * (1 - keep) * weights[i] / tot)
                    if part <= 0:
                        continue
                    tree_edges.append(tx(a, child, part, t))
                    tree_nodes.append((child, d + 1, t))
                    truth["relevant"].add(child)
                    n_nodes += 1
                    nxt.append((child, part, t, d + 1))
            frontier = nxt
        # one deep branch deposits into an exchange-like hub (benign service that receives incident value)
        deep = max(tree_nodes, key=lambda x: (x[1], x[2])) if tree_nodes else (origin, 0, t0)
        dep_t = deep[2] + rng.randint(60, 300)
        tx(deep[0], service, ETH // 2 if deep[0] != origin else ETH, dep_t)
        truth["service"].add(service)
        for i in range(4):  # the exchange later pays unrelated customers: reachable, but benign
            w = addr(f"exchange-withdrawal-{i}")
            tx(service, w, rng.randint(1, 20) * ETH // 10, dep_t + rng.randint(600, 3600))
            truth["benign"].add(w)
        # confounder: early relays pay small fees to a DEX-router-like hub used by many unrelated users.
        # It is only 2 hops from the origin, so a hop-distance baseline ranks it highly.
        router = addr("dex-router")
        for (n, d, t) in [x for x in tree_nodes if x[1] <= 2][:4]:
            tx(n, router, ETH // 200, t + rng.randint(5, 60))
        for i in range(10):
            tx(addr(f"router-user-{i}"), router, rng.randint(1, 9) * ETH // 100, t0 + rng.randint(0, window))
        truth["service"].add(router)
        mention["router"] = router
        if cfg["include_token_leg"] and tree_nodes:
            a = tree_nodes[0][0]
            swap_to = addr("token-recipient")
            tok(a, swap_to, rng.randint(10_000, 90_000) * 10 ** 6, tree_nodes[0][2] + 30)
            truth["relevant"].add(swap_to)
        if cfg["include_cycle"] and len(tree_nodes) >= 3:
            a, b = tree_nodes[-1], tree_nodes[0]
            tx(a[0], b[0], ETH // 10, max(a[2], b[2]) + 600)  # later transfer back up the tree -> directed cycle
        if cfg["repeated_edges"] and tree_edges:
            for i in range(min(cfg["repeated_edges"], len(tree_edges))):
                e = tree_edges[i]
                tx(e["from"], e["to"], int(e["value"]) // 50 + 1, int(e["timeStamp"]) + 90)
        rel_sorted = sorted(tree_nodes, key=lambda x: (x[1], x[0]))
        mention["deep1"] = rel_sorted[-1][0] if rel_sorted else origin
        mention["mid"] = rel_sorted[len(rel_sorted) // 2][0] if rel_sorted else origin
        mention["tx"] = tree_edges[min(2, len(tree_edges) - 1)]["hash"] if tree_edges else None
    elif kind == "benign":
        users = [addr(f"user-{i}") for i in range(max(4, cfg["branching"] * cfg["depth"] * 2))]
        t = t0
        step = max(600, window // (len(users) + 1))
        for i, u in enumerate(users):
            t += step + rng.randint(0, step // 3)
            tx(origin, u, rng.randint(1, 40) * ETH // 10, t)
            truth["benign"].add(u)
            if rng.random() < 0.3:  # a few users later move funds slowly
                tx(u, addr(f"user-dest-{i}"), ETH // 100, t + rng.randint(8, 30) * 3600)
        for i in range(6):  # funding of the payout wallet by its operator
            tx(addr(f"operator-{i % 2}"), origin, 50 * ETH, t0 - 86400 + i * 3600)
        truth["service"].add(origin)
        mention["deep1"] = users[0]
        mention["mid"] = users[1]
        mention["tx"] = native[0]["hash"]
    else:  # insufficient
        a, b = addr("recipient-a"), addr("recipient-b")
        tx(origin, a, 3 * ETH, t0 + 3600)
        tx(origin, b, 2 * ETH, t0 + 3 * 3600)
        tx(a, addr("recipient-c"), ETH, t0 + 6 * 3600)
        mention["deep1"], mention["mid"], mention["tx"] = a, b, None

    # noise, disconnected components, self-loop, duplicates, failed tx
    noise = [addr(f"noise-{i}") for i in range(cfg["noise_addresses"])]
    truth["noise"].update(noise)
    if len(noise) >= 2:
        for i in range(cfg["noise_transfers"]):
            u, v = rng.sample(noise, 2)
            tx(u, v, rng.randint(1, 500) * ETH // 100, t0 + rng.randint(0, window))
    for k in range(cfg["disconnected_components"]):
        tx(addr(f"island-{k}-a"), addr(f"island-{k}-b"), rng.randint(1, 9) * ETH, t0 + rng.randint(0, window))
    if cfg["include_self_loop"] and noise:
        tx(noise[0], noise[0], ETH // 3, t0 + rng.randint(0, window))
    if cfg["failed_transactions"]:
        for i in range(cfg["failed_transactions"]):
            tx(origin, addr(f"failed-{i}"), ETH, t0 + 30 + i, err=True)
    if kind == "incident":  # a benign stream into the exchange-like hub (fan-in)
        for i in range(max(6, cfg["noise_addresses"] // 2)):
            tx(addr(f"exchange-customer-{i}"), service, rng.randint(1, 30) * ETH // 10, t0 + rng.randint(0, window))
    decoy = None
    if cfg["osint_ambiguous"] and kind in ("incident", "insufficient"):
        target = mention["mid"]
        decoy = target[:6] + hashlib.sha256(f"decoy:{salt}".encode()).hexdigest()[:33] + target[-3:]
        assert len(decoy) == 42
        tx(decoy, addr("decoy-dest"), ETH // 7, t0 + rng.randint(0, window))
        truth["noise"].add(decoy)
    for i in range(min(cfg["duplicate_records"], len(native))):
        native.append(dict(native[(i * 7) % len(native)]))

    # ---------------- OSINT documents (raw; provenance completed by Person 1 ingestion) -----------
    base = f"https://synthetic.astra.invalid/sim/{seed}/{kind}"
    docs: list[dict] = []

    def doc(source_id, name, stype, group, path, title, pub, ret, rel, rationale, text, **kw):
        docs.append({"source_id": source_id, "source_name": name, "source_type": stype, "publisher_group": group,
                     "url": f"{base}/{path}", "title": title, "published_at": None if pub is None else _iso(pub),
                     "retrieved_at": _iso(ret), "reliability": rel, "reliability_rationale": rationale,
                     "text": text, **kw})

    report_text = None
    if kind == "incident" and cfg["osint_incident_report"]:
        report_text = (f"SYNTHETIC INCIDENT REPORT. An exploit was observed at {_iso(t0)}. The attacker wallet {origin} "
                       f"drained the protocol and stolen funds moved through several relays. Investigators observed "
                       f"stolen funds arriving at {mention['deep1']}. A relay of the exploit flow was {mention['mid']}. "
                       "No person or organisation has been identified.")
        doc("sim-report", "Synthetic Incident Report", "threat_report", "sim-security-firm", "report", "Exploit initial findings",
            t0 + 2 * 3600, t0 + 2 * 3600 + 600, 0.8, "Rubric: security publisher, first-hand monitoring.", report_text)
    if cfg["osint_service_label"]:
        lab = service if kind == "incident" else origin
        doc("sim-labels", "Synthetic Label Registry", "label_registry", "sim-labels", "labels/1", "Exchange label",
            t0 - 60 * 86400, t0 + 3 * 3600, 0.75, "Rubric: curated label set; can be outdated.",
            f"Address {lab} is labelled as a centralized exchange hot wallet."
            + (f" Address {mention['router']} is labelled as a DEX router contract used by many traders."
               if mention.get("router") else ""))
        if kind == "benign":
            doc("sim-status", "Synthetic Exchange Status Page", "other", "sim-exchange-operator", "status",
                "Withdrawal wallet notice", t0 - 10 * 86400, t0 + 3 * 3600, 0.7, "Rubric: operator's own public notice.",
                f"Our exchange processes customer withdrawals from hot wallet {origin}. Payouts are batched.")
    if kind == "incident" and cfg["osint_contradiction"]:
        doc("sim-otc", "Synthetic OTC Directory", "label_registry", "sim-otc-directory", "otc", "OTC desk listing",
            t0 + 5 * 3600, t0 + 5 * 3600 + 300, 0.5, "Rubric: self-reported directory listing.",
            f"{mention['mid']} is listed as an OTC desk service wallet in this directory.")
    if cfg["osint_stale"] and noise:
        doc("sim-forum", "Synthetic Forum", "forum", "sim-forum", "forum/1", "Old phishing thread",
            t0 - 400 * 86400, t0 + 4 * 3600, 0.3, "Rubric: anonymous forum post.",
            f"Old warning: {noise[-1]} was involved in a phishing scam.")
    if cfg["osint_missing_timestamp"] and mention.get("tx"):
        doc("sim-analyst", "Synthetic Analyst Note", "analyst_note", "sim-analyst", "note/1", "Transaction note",
            None, t0 + 4 * 3600 + 1800, 0.5, "Rubric: internal note; publication time not recorded.",
            f"Transaction {mention['tx']} looks like part of the exploit dispersal.")
    if decoy is not None:
        tgt = mention["mid"]
        doc("sim-chat", "Synthetic Public Chat Log", "forum", "sim-chat", "chat/1", "Shortened address mention",
            t0 + 6 * 3600, t0 + 6 * 3600 + 120, 0.3, "Rubric: public chat; shortened addresses only.",
            f"Someone said 0x{tgt[2:6]}…{tgt[-3:]} is connected to the exploit.")
    if report_text and cfg["osint_duplicate"]:
        doc("sim-mirror", "Synthetic News Mirror", "threat_report", "sim-news-mirror", "mirror", "Republished report",
            t0 + 7 * 3600, t0 + 7 * 3600 + 60, 0.6, "Rubric: aggregator republishing.", report_text)
    if cfg["osint_outage"]:
        doc("sim-feed", "Synthetic Threat Feed", "threat_feed", "sim-feed", "feed", None, None, t0 + 3600, 0.6,
            "Rubric: automated feed.", "", collection_status="failed", error="HTTP 503 (simulated provider outage)")

    expected = {"incident": "H1", "benign": "H2", "insufficient": "H3"}[kind]
    meta_cfg = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in cfg.items()}
    h = config_hash(meta_cfg)
    return {
        "scenario_id": f"sim-{kind}-{seed}-{h}", "start_address": origin, "chain_id": 1,
        "native_rows": native, "erc20_rows": erc20, "documents": docs,
        "metadata": {"generator": "astra-sim-v1", "seed": seed, "scenario_kind": kind, "config": meta_cfg,
                     "config_hash": h, "start_time": _iso(t0), "window_hours": cfg["window_hours"],
                     "raw_native_rows": len(native), "raw_erc20_rows": len(erc20), "documents": len(docs),
                     "label": "SYNTHETIC / SIMULATED"},
        "ground_truth": {"relevant_addresses": sorted(truth["relevant"]), "service_addresses": sorted(truth["service"]),
                         "benign_addresses": sorted(truth["benign"]), "noise_addresses": sorted(truth["noise"]),
                         "expected_case_hypothesis": expected,
                         "definition": "relevant = addresses that received incident value along the generated "
                                       "splitting tree (plus the token-leg recipient); service hubs are NOT relevant."},
    }
