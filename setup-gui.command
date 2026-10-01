#!/usr/bin/env bash
# Ridikc Content Harvester - installer macOS / Linux
# Memeriksa Python, memasang dependency, yt-dlp, dan membuat shortcut desktop.
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo " Ridikc Content Harvester - Setup"
echo "============================================"
echo

PY="python3"
if ! command -v python3 >/dev/null 2>&1; then
  PY="python"
fi

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[1/4] Python tidak ditemukan."
  echo "  macOS : brew install python@3.12   (atau unduh dari python.org)"
  echo "  Linux : sudo apt install python3 python3-pip"
  exit 1
fi

MAJOR="$("$PY" -c 'import sys; print(sys.version_info[0])')"
if [ "$MAJOR" -lt 3 ]; then
  echo "[GAGAL] Python 3.10+ diperlukan."
  exit 1
fi
echo "[1/4] $("$PY" --version) - OK"
echo

echo "[2/4] Memasang dependency dari requirements.txt..."
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r requirements.txt
echo

echo "[3/4] Memperbarui yt-dlp ke versi terbaru..."
"$PY" -m pip install --upgrade yt-dlp --quiet
"$PY" -m yt_dlp --version >/dev/null 2>&1 \
  && echo "yt-dlp - OK" \
  || echo "[PERINGATAN] yt-dlp tidak terverifikasi."
echo

echo "[4/4] Membuat launcher di Desktop..."
if [ "$(uname)" = "Darwin" ]; then
  DESKTOP="$HOME/Desktop"
  TARGET="$DESKTOP/RCH-GUI.command"
  mkdir -p "$DESKTOP"
  cp launchers/RCH-GUI.command "$TARGET"
  chmod +x "$TARGET" launchers/rch-gui.sh
  echo "Launcher: $TARGET"
  echo "Catatan: klik pertama kali akan minta konfirmasi Gatekeeper."
else
  chmod +x launchers/rch-gui.sh
  echo "Launcher: $(pwd)/launchers/rch-gui.sh"
  echo " Tambahkan ke menu aplikasi sistem Anda bila diinginkan."
fi
echo

echo "============================================"
echo " Selesai. Jalankan launchers/rch-gui.sh"
echo " GUI terbuka di http://127.0.0.1:8787"
echo "============================================"
