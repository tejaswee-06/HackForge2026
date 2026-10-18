#!/usr/bin/env bash
# ASTRA frontend (macOS/Linux): dev server on http://localhost:5173 proxying /api and /health to the backend
set -euo pipefail
cd "$(dirname "$0")/frontend"
[ -d node_modules ] || npm install
exec npm run dev
