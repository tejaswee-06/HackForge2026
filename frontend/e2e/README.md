# ASTRA UI end-to-end checks (Playwright, optional)

These scripts drive the real UI against a running integrated stack (backend on :8000, `npm run preview`
on :4173). They are not part of `npm run build` so the frontend keeps its original dependency set.

```bash
cd frontend/e2e
npm init -y && npm install playwright@1.48.2   # one-time; or use any Playwright >= 1.48
npx playwright install chromium               # or set ASTRA_CHROMIUM=/path/to/chromium-or-headless_shell
node ui-inventory.cjs http://127.0.0.1:4173 ./out/inventory     # route/control inventory + screenshots
node ui-interactions.cjs http://127.0.0.1:4173 ./out/interactions # 12 scripted checks (exit code 1 on failure)
# stop the backend, then:
node ui-backend-down.cjs http://127.0.0.1:4173 ./out            # verifies the explicit "backend unreachable" state
```
