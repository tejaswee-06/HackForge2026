> **Integration note (final package):** this is Person 1's original contract. The five Person 2 endpoints it lists as `501` stubs are now implemented by the integrated engine - see `docs/API_CONTRACTS.md`.

# ASTRA Person 1 — API handover (Persons 2 & 3)

Every JSON sample below was captured from the **running server** by
`scripts/clean_start_check.py --save-examples`; full payloads are in `docs/examples/`.
Interactive docs: `http://127.0.0.1:8000/docs` (OpenAPI at `/openapi.json`).

## Data-mode semantics (every response has `data_mode`)
| value | meaning |
|---|---|
| `synthetic` | Invented test data (the deterministic scenario). Never presented as live. |
| `real` | Fetched just now from a live provider (Etherscan V2). |
| `cached` | Served from the provider TTL cache; `sources[].retrieved_at` is the *original* retrieval time. |

There is **no silent fallback**: a wallet query with the provider down/unconfigured returns an error envelope
(`degraded_mode: true`), never synthetic data under a `real` label.

## Common response fields
`investigation_id, data_mode, analysis_version, generated_at, data_cutoff, limitations[], sources[]`
(`data_cutoff` = latest event timestamp in the data; `null` when empty).

## Error envelope (all non-2xx)
```json
{"error": {"code": "PROVIDER_NOT_CONFIGURED", "message": "...", "retryable": false,
           "degraded_mode": true, "provider": "etherscan", "details": null}}
```
Codes: `VALIDATION_ERROR`(422) `INVESTIGATION_NOT_FOUND`/`SCENARIO_NOT_FOUND`/`NOT_FOUND`(404) `UNSUPPORTED_CHAIN`/`UNBOUNDED_REQUEST`(400)
`PROVIDER_NOT_CONFIGURED`/`PROVIDER_RATE_LIMITED`/`PROVIDER_UNAVAILABLE`/`PROVIDER_RETRY_EXHAUSTED`(503) `PROVIDER_AUTH`/`PROVIDER_BAD_RESPONSE`(502)
`NOT_IMPLEMENTED`(501, Person 2 endpoints) `INTERNAL_ERROR`(500).

## Endpoints
| method | path | status |
|---|---|---|
| GET | `/health` | built, tested |
| GET | `/api/scenarios` | built, tested |
| POST | `/api/investigations` | built, tested |
| GET | `/api/investigations/{id}` | built, tested |
| GET | `/api/investigations/{id}/graph` (`asset_id,start_time,end_time,max_edges`) | built, tested |
| GET | `/api/investigations/{id}/paths` (`start,max_depth,max_results,asset_id,start_time,end_time,enforce_time_order`) | built, tested |
| GET | `/api/investigations/{id}/transactions` (`asset_id,limit,offset,include_non_success`) | built, tested — **Person 2 input** |
| GET | `.../leads` `.../evidence` `.../replay` `.../report`, POST `.../sensitivity` | **501 stubs** — Person 2 owns the logic |

Demo case id (pre-created at startup, offline): `inv-ghost_attacks_synthetic_v1`. Person 3 can render it with no credentials.

### Scenario (`GET /api/scenarios` → `scenarios[0]`)
```json
{
  "chain_id": 1,
  "data_mode": "synthetic",
  "dataset_version": "1.0.0",
  "description": "Small deterministic simulated incident: a split, a multi-hop path, a cycle, a duplicate record, a failed transaction, an unlike-token transfer and a disconnected pair. Amounts are scaled-down placeholders.",
  "event_count": 13,
  "investigation_id": "inv-ghost_attacks_synthetic_v1",
  "limitations": [
    "Simulated incident; results demonstrate the pipeline, not real-world attribution.",
    "Graph connectivity and timing are observed relationships, not proof of common ownership."
  ],
  "name": "Ghost Attacks - simulated $42M exploit flow (SYNTHETIC)",
  "scenario_id": "ghost_attacks_synthetic_v1",
  "start_address": "0x000000000000000000000000000000000000a000",
  "synthetic": true
}
```

### Create investigation — request
Scenario: `{"scenario_id": "ghost_attacks_synthetic_v1"}`
Bounded wallet query (needs `ETHERSCAN_API_KEY`):
```json
{"chain_id": 1, "address": "0x00000000000000000000000000000000000a0001", "max_transfers": 100,
  "start_time": "2026-10-01T00:00:00Z", "end_time": "2026-10-02T00:00:00Z"}
```
Limits: chain must be supported (Ethereum `1`), `max_transfers` 1..500, address `0x`+40 hex, `start_time<=end_time`, tz-aware times. When time filters are supplied, ASTRA filters the bounded provider sample after retrieval and adds an explicit limitation to the case response; this is **not an exhaustive historical-window query**. A provider/block-range adapter is needed for exhaustive historical time windows.
Creation is idempotent: the same request yields the same `investigation_id`.

### Canonical transaction (`NormalizedTransfer`) — the only shape Person 2 should consume
```json
{
  "asset_id": "1:native",
  "asset_symbol": "ETH",
  "asset_type": "native",
  "block_number": 18000001,
  "chain_id": 1,
  "event_id": "1:0x5df55d6f9cf7f138a367162a49011c0397168c5cfc877fc44b002509f1d5db80:tx",
  "event_type": "native_transfer",
  "from_address": "0x000000000000000000000000000000000000a000",
  "log_index": null,
  "normalized_value": "100.000000000000000000",
  "raw_value": "100000000000000000000",
  "source": {
    "data_mode": "synthetic",
    "provider": "astra-fixture",
    "reference": "ghost_attacks_synthetic_v1@1.0.0",
    "retrieved_at": "2026-10-01T00:00:00Z"
  },
  "status": "success",
  "timestamp": "2026-10-01T02:14:00Z",
  "to_address": "0x000000000000000000000000000000000000a001",
  "token_contract": null,
  "token_decimals": 18,
  "transaction_hash": "0x5df55d6f9cf7f138a367162a49011c0397168c5cfc877fc44b002509f1d5db80"
}
```
Rules: addresses/hashes lower-case; `raw_value` is an exact integer **string** (never float);
`normalized_value` only when `token_decimals` is known (else `null`); `null` means *unknown*, `"0"` means zero;
`asset_id` is `"<chain>:native"` or `"<chain>:erc20:<contract>"` — **never sum across different `asset_id`s**;
`event_id = "<chain>:<tx_hash>:<tx|log-N|tok-…>"` is the stable identity.
Failed transactions are retained in `/transactions?include_non_success=true` but excluded from the graph.

### Graph node / edge
```json
{
  "address": "0x000000000000000000000000000000000000a000",
  "chain_id": 1,
  "first_seen": "2026-10-01T02:14:00Z",
  "gross_in_raw": {},
  "gross_out_raw": {
    "1:native": "160000000000000000000"
  },
  "in_transfer_count": 0,
  "last_seen": "2026-10-01T02:15:00Z",
  "node_id": "1:0x000000000000000000000000000000000000a000",
  "out_transfer_count": 2
}
```
```json
{
  "asset_id": "1:native",
  "asset_symbol": "ETH",
  "asset_type": "native",
  "block_number": 18000001,
  "chain_id": 1,
  "edge_id": "1:0x5df55d6f9cf7f138a367162a49011c0397168c5cfc877fc44b002509f1d5db80:tx",
  "event_type": "native_transfer",
  "log_index": null,
  "normalized_value": "100.000000000000000000",
  "provenance": {
    "data_mode": "synthetic",
    "provider": "astra-fixture",
    "reference": "ghost_attacks_synthetic_v1@1.0.0",
    "retrieved_at": "2026-10-01T00:00:00Z"
  },
  "raw_value": "100000000000000000000",
  "source": "1:0x000000000000000000000000000000000000a000",
  "status": "success",
  "target": "1:0x000000000000000000000000000000000000a001",
  "timestamp": "2026-10-01T02:14:00Z",
  "token_contract": null,
  "token_decimals": 18,
  "transaction_hash": "0x5df55d6f9cf7f138a367162a49011c0397168c5cfc877fc44b002509f1d5db80"
}
```
`edge_id == event_id` (every edge points back to one observed transfer). `gross_in_raw/gross_out_raw` are **gross per-asset
sums of edge values** (value passing through several wallets is counted once per edge) — not unique value, not fiat.

### Path (`GET .../paths`)
```json
{
  "asset_id": "1:native",
  "bottleneck_normalized": "30.000000000000000000",
  "bottleneck_raw": "30000000000000000000",
  "edges": [
    {
      "event_id": "1:0x371e86b7f7af030792b30f871eb37239604960826465f7a467fcec77661973c4:tx",
      "from_node": "1:0x000000000000000000000000000000000000a000",
      "log_index": null,
      "normalized_value": "60.000000000000000000",
      "raw_value": "60000000000000000000",
      "timestamp": "2026-10-01T02:15:00Z",
      "to_node": "1:0x000000000000000000000000000000000000a002",
      "transaction_hash": "0x371e86b7f7af030792b30f871eb37239604960826465f7a467fcec77661973c4"
    },
    {
      "event_id": "1:0x8778f5bc64ce21a7d8c06289ef09dfee70e076b79d49ebfd54d1a4da3f60be65:tx",
      "from_node": "1:0x000000000000000000000000000000000000a002",
      "log_index": null,
      "normalized_value": "30.000000000000000000",
      "raw_value": "30000000000000000000",
      "timestamp": "2026-10-01T02:20:00Z",
      "to_node": "1:0x000000000000000000000000000000000000a004",
      "transaction_hash": "0x8778f5bc64ce21a7d8c06289ef09dfee70e076b79d49ebfd54d1a4da3f60be65"
    }
  ],
  "end_time": "2026-10-01T02:20:00Z",
  "event_ids": [
    "1:0x371e86b7f7af030792b30f871eb37239604960826465f7a467fcec77661973c4:tx",
    "1:0x8778f5bc64ce21a7d8c06289ef09dfee70e076b79d49ebfd54d1a4da3f60be65:tx"
  ],
  "hops": 2,
  "node_ids": [
    "1:0x000000000000000000000000000000000000a000",
    "1:0x000000000000000000000000000000000000a002",
    "1:0x000000000000000000000000000000000000a004"
  ],
  "path_id": "daf224d89118",
  "start_time": "2026-10-01T02:15:00Z",
  "terminated_reason": "leaf"
}
```
Paths are single-asset, time-ordered (set `enforce_time_order=false` to disable), never revisit a wallet, and are bounded by
`max_depth`/`max_results`. `terminated_reason`: `leaf` | `max_depth` | `cycle_blocked`.
`bottleneck_raw` = smallest edge value on the path = an **upper bound** on value that could have traversed it, not proven flow.

## Known limitations
* Graph connectivity and timing are observed relationships — **not proof of common ownership or identity**.
* Wallet queries return only the queried address's own transfers (one hop) from `txlist` + `tokentx`; internal ETH transfers
  and bridges/mixers are not handled (mixer/bridge boundaries must be marked unresolved by downstream logic).
* Optional `start_time`/`end_time` filters are applied after fetching the bounded provider sample. They can omit events that fall outside the sample; do not treat these results as exhaustive for a historical window.
* Etherscan `tokentx` has no `logIndex`; such events get a deterministic `tok-…` id, so two byte-identical transfers inside one
  transaction collapse into one event.
* Investigations are in-memory (lost on restart; demo cases are recreated automatically).
* The live Etherscan adapter is tested against mocked HTTP only; it has **not** been run against the real API in this build.
* Addresses are stored lower-case (EIP-55 checksum casing is not reproduced).
