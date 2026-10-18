#!/usr/bin/env bash
# ASTRA backend (macOS/Linux). Creates backend/.venv on first run, then serves http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")/backend"
PY=${PYTHON:-python3}
if [ ! -d .venv ]; then "$PY" -m venv .venv; ./.venv/bin/python -m pip install --upgrade pip; ./.venv/bin/pip install -r requirements-dev.txt; fi
ENVFILE=""; [ -f ../.env ] && ENVFILE="--env-file ../.env"
exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${ASTRA_PORT:-8000}" $ENVFILE
