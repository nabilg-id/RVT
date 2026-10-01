#!/usr/bin/env bash
# Ridikc Content Harvester - launcher macOS / Linux
# Menjalankan GUI web lokal dan membukanya di browser.
set -euo pipefail

cd "$(dirname "$0")/.."

PY="python3"
if ! command -v python3 >/dev/null 2>&1; then
  PY="python"
fi

if ! "$PY" -c "import flask" >/dev/null 2>&1; then
  echo "[RCH] Dependency belum terpasang. Jalankan setup-gui.sh terlebih dahulu."
  exit 1
fi

echo "[RCH] Menjalankan GUI di http://127.0.0.1:8787"
echo "[RCH] Tekan Ctrl+C untuk menghentikan server."
exec "$PY" -m rch.web
