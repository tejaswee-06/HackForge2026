# Remaining work, blockers and known limitations

## BLOCKED (external dependency)
| Item | Why | What is needed |
|---|---|---|
| Live Etherscan verification | No API key was available; the key found in Person 1's ZIP was deliberately **not used** (it should be rotated) | Set `ETHERSCAN_API_KEY` in `.env`, create a wallet investigation, compare a few transfers with a block explorer |
| Live GitHub OSINT verification | Disabled by default; not exercised against api.github.com in this build | `ASTRA_OSINT_GITHUB_ENABLED=1` (+ optional fine-grained `GITHUB_TOKEN`), run "Collect public sources" on a live wallet case |
| Elliptic++ model training (`astra_intel/train.py`) | Dataset not downloadable here; Bitcoin data, not Ethereum | Download the CSVs, `pip install -r intel_training/requirements-training.txt`, run `python -m astra_intel.train` from `backend/`; any model must be evaluated before use and kept separate from the priority score |
| Frontend lint / type-check / unit tests | Not configured in the original frontend | Add ESLint/TypeScript/Vitest if the team wants them; the UI is covered by the Playwright scripts in `frontend/e2e/` |

## PARTIAL
* **Persistence**: in-memory only (no SQLite/PostgreSQL, no migrations). Restarts drop live/simulated cases.
* **Live wallet scope**: one-hop neighbourhood from Etherscan (`txlist`+`tokentx`); internal transfers
  and exhaustive historical windows are not covered (stated in `limitations`).
* **Indicator types**: EVM addresses, tx hashes, shortened forms, BTC patterns. No alias/entity-name
  resolution, no repository-artifact parsing beyond issue text.
* **Stance classification** is a keyword/negation heuristic; sarcasm, quotes of other sources and
  complex negation are not understood. Findings remain "source-reported" claims either way.
* **Calibration**: priority weights (astra-priority-v1) and H3 decisiveness constants are hand-set and
  documented; they are validated only on the synthetic generator.
* **Frontend**: JSX (not TypeScript) and single-page state navigation are preserved from Person 3's
  final design rather than re-architected; the graph view shows the top leads (expandable by 8) rather
  than the whole graph.
* **Report**: JSON + printable HTML; PDF is produced by the browser's print dialog.
* **Analyst review records / audit trail** (BP 7.4 "Review record") are not implemented.

## MISSING (not implemented)
* Real labelled incidents for evaluation; bridge-mapping and mixer scenarios beyond the "privacy
  boundary" stance (no cross-chain support at all — Ethereum only).
* Cash-Out Interception Radar page (stretch USP) — only per-lead `next_check` exists.
* Authentication/authorisation for the API (do not expose publicly with live keys).

## UNVERIFIED
* Windows PowerShell start scripts (`start-backend.ps1`, `start-frontend.ps1`): written to mirror the
  verified macOS/Linux scripts, but no PowerShell was available in the build environment to execute them.
* Behaviour on very large live graphs (tested synthetically up to 100k edges for DSA and ~21k events
  end-to-end).
* Browser support beyond headless Chromium (the original WebGL background effects were not modified).

## Known limitations that affect interpretation
* Scores are prioritisation aids, not probabilities; hypotheses are review scores.
* Graph connectivity, timing and FIFO allocation never prove ownership or that the same funds moved.
* Synthetic benchmark results (precision@10 = 1.0, case accuracy = 1.0) reflect a generator built by the
  same team; they are a consistency check, not evidence of real-world performance.
