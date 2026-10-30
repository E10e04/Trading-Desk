#!/usr/bin/env bash
# Construit l'application de bureau pour la plateforme COURANTE (PyInstaller et Electron ne compilent pas en croisé).
#   macOS (Intel x86_64) : ./scripts/build-desktop.sh mac     Windows (Git Bash) : ./scripts/build-desktop.sh win
set -euo pipefail
TARGET="${1:?usage: build-desktop.sh mac|win}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
( cd "$ROOT/frontend" && npm ci && npm run build )
cd "$ROOT/backend"
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
"$PY" -m pip install -q -r requirements.lock.txt pyinstaller==6.11.1
"$PY" -m PyInstaller --noconfirm --distpath build-dist --workpath build-work trading-backend.spec
rm -rf "$ROOT/desktop/build/backend" && mkdir -p "$ROOT/desktop/build" && cp -R build-dist/trading-backend "$ROOT/desktop/build/backend"
cd "$ROOT/desktop" && npm ci && npm run "dist:$TARGET"
echo "Installateur dans desktop/release/"
