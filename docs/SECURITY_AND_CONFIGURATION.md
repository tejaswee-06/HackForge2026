# Security and configuration

## Environment variables (backend, all optional)
| Variable | Default | Purpose |
|---|---|---|
| `ETHERSCAN_API_KEY` | – | live wallet queries (server-side only; redacted from errors, never returned) |
| `ETHERSCAN_BASE_URL` | `https://api.etherscan.io/v2/api` | provider endpoint |
| `ASTRA_PROVIDER_TIMEOUT_S` / `ASTRA_PROVIDER_MAX_RETRIES` / `ASTRA_PROVIDER_BACKOFF_S` | 10 / 3 / 0.5 | provider reliability (also used for the GitHub adapter timeout) |
| `ASTRA_CACHE_TTL_SECONDS` | 300 | provider cache (0 disables); cached results labelled `cached` |
| `ASTRA_SUPPORTED_CHAINS` | 1 | Ethereum-first |
| `ASTRA_MAX_WALLET_TRANSFERS` | 500 | bound on wallet queries |
| `ASTRA_OSINT_GITHUB_ENABLED` | 0 | enable live GitHub issue search |
| `GITHUB_TOKEN` | – | optional; use a fine-grained token with no repository permissions |
| `ASTRA_CORS_ORIGINS` | localhost/127.0.0.1 on 5173 and 4173 | browser origins; `*` is ignored on purpose |
| `ASTRA_MAX_INVESTIGATIONS` | 200 | in-memory bound (simulations + wallet cases) |
| `ASTRA_EVALUATION_SEEDS` | 1,2,3,4,5 | seeds for `GET /api/evaluation` |
| `ASTRA_DATA_DIR` | `./data` | scenarios + OSINT fixtures |

Frontend (`frontend/.env.example`): `VITE_ASTRA_DATA_SOURCE` (`api`|`fixtures`), `VITE_ASTRA_API_BASE_URL`
(empty = same-origin via Vite proxy), `ASTRA_BACKEND_URL` (proxy target, not bundled). **Never put
secrets in `VITE_*` variables** — they are compiled into the browser bundle.

## Audit findings and fixes
| Area | Finding | Action |
|---|---|---|
| **Secrets** | Person 1's ZIP contained a populated `.env` with a 34-character `ETHERSCAN_API_KEY` | Not copied into the integrated package; `.env` is git-ignored; `.env.example` has empty values. **Rotate that Etherscan key** — it was distributed in the source ZIP. |
| Secrets | Person 1's ZIP also contained a Windows `.venv` and `.pytest_cache` | excluded |
| Secret leakage | keys/tokens in health, errors, logs | tests assert the Etherscan key and GitHub token never appear in `/health`, OSINT responses or errors; provider errors are redacted; tokens only in request headers |
| CORS | P1 had no CORS (frontend could not call it cross-origin) | explicit allow-list, GET/POST only, no credentials; wildcard refused; Vite proxy keeps dev same-origin |
| Input validation | new endpoints | Pydantic models with bounds (`extra=forbid`), address/hash regexes, timezone-aware timestamps required, http(s)-only URLs (rejects `javascript:`/`file:`), query ranges (`max_hops≤12`, `max_nodes≤500`, `max_edges≤5000`, `k≤100`) |
| Unbounded computation | graph traversal | hop/node/expansion budgets on every traversal, iterative algorithms, candidate cap (`max_candidates`), parcel cap in flow decomposition, ≤500 docs/case, ≤200k chars/doc, ≤6 live terms, simulation size caps, investigation cap, LRU analysis cache (32) |
| XSS / unsafe report | source excerpts are attacker-controllable | React escapes all text; HTML report escapes every value (`html.escape`) and is served with `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`; test injects `<script>` into a source and asserts it is escaped |
| Prompt/content injection | OSINT text | treated as data only; deterministic regex extraction; no LLM; no execution |
| Path traversal | fixture loading | file names come from server-side scenario ids only, never from request paths |
| Links | external sources | opened with `noopener,noreferrer`; synthetic `.invalid` URLs are announced instead of opened |
| Misleading labels | P3 UI had hardcoded "LIVE" badge, "Systems nominal", fake rate-limit notification, fake metrics | replaced by backend-derived values and explicit data-mode labels in API mode |
| Misleading metrics | Evaluation page showed unverifiable Elliptic++ numbers | API mode shows only measured benchmark values with denominators; fixture mode keeps its original caveat |
| Dependency hygiene | – | no new runtime dependencies (backend: FastAPI, Pydantic, NetworkX, httpx, uvicorn; frontend unchanged + Vite config file). Tested versions in `backend/requirements-tested.txt` |
| Logging | – | unhandled errors logged server-side without request bodies; responses return a generic `INTERNAL_ERROR` |

## Deployment considerations
* Run behind a TLS reverse proxy; restrict `ASTRA_CORS_ORIGINS` to the deployed frontend origin.
* The API has **no authentication** (hackathon MVP). Do not expose it publicly with live provider keys
  configured; add an auth layer or network restriction first.
* In-memory state: restarts drop wallet/simulation investigations (scenarios are rebuilt; simulations
  are reproducible from their seed).
* Respect provider terms and rate limits; collect only public data needed for the case; human review is
  required before any consequential action — ASTRA does not identify people.
