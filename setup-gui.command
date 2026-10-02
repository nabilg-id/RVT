#!/usr/bin/env bash
# Ridikc Video Toolkit - installer macOS / Linux
set -euo pipefail
cd "$(dirname "$0")"

echo "============================================"
echo " Ridikc Video Toolkit - Setup"
echo "============================================"
echo

PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[1/4] Python tidak ditemukan."
  echo "  macOS : brew install python@3.12"
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

echo "[2/4] Memasang dependency dasar + pytest..."
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r requirements-dev.txt
echo

echo "[3/4] Memasang dependensi clip (torch, moviepy, whisper, mediapipe)."
echo "      Ini besar - mungkin perlu 15-30 menit dan ~3 GB disk."
echo "      Tekan Ctrl+C untuk melewati dan hanya memakai mode ringan."
"$PY" -m pip install -r requirements.txt || \
  echo "[PERINGATAN] Dependensi clip gagal; pipeline clip tidak akan jalan."
echo

echo "[4/4] Membuat launcher..."
if [ "$(uname)" = "Darwin" ]; then
  TARGET="$HOME/Desktop/VCLIP-GUI.command"
  mkdir -p "$HOME/Desktop"
  cp launchers/VCLIP.command "$TARGET"
  chmod +x "$TARGET" launchers/vclip.sh
  echo "Launcher: $TARGET"
  echo "Catatan: klik pertama kali akan minta konfirmasi Gatekeeper."
else
  chmod +x launchers/vclip.sh
  echo "Launcher: $(pwd)/launchers/vclip.sh"
fi
echo

echo "============================================"
echo " Selesai. Jalankan launchers/vclip.sh"
echo " GUI: http://127.0.0.1:8787"
echo " CLI: $PY -m clipper.main"
echo "============================================"