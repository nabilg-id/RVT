#!/usr/bin/env bash
# Ridikc Video Toolkit - launcher macOS / Linux (GUI clipper)
set -euo pipefail

cd "$(dirname "$0")/.."

PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"

if ! "$PY" -c "import flask, dotenv" >/dev/null 2>&1; then
  echo "[RCH] Dependency dasar belum ada. Jalankan:"
  echo "       $PY -m pip install -r requirements-dev.txt"
  echo "  Untuk fitur clip penuh:"
  echo "       $PY -m pip install -r requirements.txt"
  exit 1
fi

if ! "$PY" -c "import moviepy.editor" >/dev/null 2>&1; then
  echo "[RCH] moviepy belum terpasang - pipeline clip tidak akan jalan."
  echo "       pasang dengan: $PY -m pip install -r requirements.txt"
  echo
fi

echo "[RCH] Membuka GUI clip di http://127.0.0.1:8787"
echo "[RCH] Tekan Ctrl+C untuk menghentikan server."
exec "$PY" -m clipper.app