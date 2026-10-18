# ASTRA — frontend (Person 3)

React + Vite analyst console. **The design is final and preserved**: layout, navigation, components,
colours, typography, animations, landing page, dock and accessibility panel are unchanged. The
integration replaced the hard-coded data with the ASTRA backend.

## Run
```bash
npm install
npm run dev        # http://localhost:5173 ; proxies /api and /health to http://127.0.0.1:8000
npm run build && npm run preview   # production build on http://localhost:4173 (same proxy)
```
Start the backend first (`../start-backend.sh` or `..\start-backend.ps1`).

## Data sources
* `VITE_ASTRA_DATA_SOURCE=api` (default): the integrated backend. Every view shows the active data mode
  (`SYNTHETIC / SIMULATED`, `LIVE`, `CACHED`).
* `VITE_ASTRA_DATA_SOURCE=fixtures`: the original illustrative fixtures (`src/services/fixtures.js`),
  labelled `UI FIXTURE`.
* If the backend is unreachable the UI shows a banner and labels the fallback fixtures `UI FIXTURE`.
* `VITE_ASTRA_API_BASE_URL` may point at a backend origin (must be in the backend's `ASTRA_CORS_ORIGINS`).
  Never put secrets in `VITE_*` variables.

## Integration files
* `src/services/api.js` — API client (timeouts, error envelopes, unreachable detection).
* `src/services/adapters.js` — pure mapping from backend JSON to the shapes the components render.
* `src/main.jsx` — components unchanged in structure; data comes from `useAstraModel()`.
* `vite.config.js` — dev/preview proxy.
* `e2e/` — optional Playwright scripts (inventory, 12 interaction checks, backend-down state).

Endpoint mapping: `../docs/API_CONTRACTS.md` (section "Frontend feature → endpoint map").
