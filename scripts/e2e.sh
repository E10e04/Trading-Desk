#!/usr/bin/env bash
# Tests e2e de l'interface : serveur de TEST (données synthétiques, base temporaire) + Playwright.
# Prérequis : backend/.venv installé, frontend construit (npm run build), paquet `playwright` + Chromium disponibles.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${E2E_PORT:-8799}"
cd "$ROOT/backend"
PY="${PYTHON:-.venv/bin/python}"
"$PY" -m tests.e2e_server --port "$PORT" > "${TMPDIR:-/tmp}/trading-e2e-server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.5; done
curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null || { echo "serveur de test indisponible"; tail -20 "${TMPDIR:-/tmp}/trading-e2e-server.log"; exit 1; }
cd "$ROOT/frontend"
E2E_URL="http://127.0.0.1:$PORT" node --test --test-timeout=60000 e2e/e2e.test.mjs
