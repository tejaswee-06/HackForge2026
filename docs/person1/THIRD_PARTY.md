# Third-party code and research notes

**No third-party source code was copied into this package.** The normalizer, provider adapter, graph engine and API are
independently written for ASTRA (using documented public concepts from the blueprint). Runtime dependencies: FastAPI, Pydantic,
NetworkX, httpx, uvicorn (all installed from PyPI under their own licenses).

Candidate repositories were shallow-cloned for a **license check only** (no code reused, no tests run except one failed
CryptTrace self-test that was missing `pip install -e .`):

| Repo | License found in clone | Decision |
|---|---|---|
| bobslayerX/crypttrace | MIT (LICENSE file) | Not reused; NOT FULLY EVALUATED |
| gl0bal01/chainmap | MIT | Not reused (TypeScript/Bun); NOT FULLY EVALUATED |
| Ciprian-LocalPulse/ofi-chain-forensics-en | MIT | Not reused; NOT FULLY EVALUATED |
| rohteemie/Open-Source-Blockchain-Forensics | MIT | Not reused; NOT FULLY EVALUATED |
| tokamak-network/Argus | MIT / Apache-2.0 files | Not reused (Rust) |
| 0xnomy/ElliptiGraph | README says MIT, **no LICENSE file** | Not reused (also ArangoDB) |
| pranithreddy12/chain-trace | **no LICENSE file found** | Do not copy code |

Code-level audits of these repos (module quality, test results) were NOT performed. If you later adopt code from any of them,
verify its license again and keep its copyright notice.
