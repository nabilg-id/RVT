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
  echo "[1/5] Python tidak ditemukan."
  echo "  macOS : brew install python@3.12"
  echo "  Linux : sudo apt install python3 python3-pip"
  exit 1
fi

MAJOR="$("$PY" -c 'import sys; print(sys.version_info[0])')"
if [ "$MAJOR" -lt 3 ]; then
  echo "[GAGAL] Python 3.10+ diperlukan."
  exit 1
fi
echo "[1/5] $("$PY" --version) - OK"
echo

echo "[2/5] Memasang dependency dasar + pytest..."
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r requirements-dev.txt
echo

echo "[3/5] Memasang dependensi clip (torch, moviepy, whisper, mediapipe)."
echo "      Ini besar - mungkin perlu 15-30 menit dan ~3 GB disk."
echo "      Tekan Ctrl+C untuk melewati dan hanya memakai mode ringan."
"$PY" -m pip install -r requirements.txt || \
  echo "[PERINGATAN] Dependensi clip gagal; pipeline clip tidak akan jalan."
echo

echo "[4/5] Memasang Ridikc Video Toolkit (perintah rch / vclip)..."
# WAJIB, bukan opsional. Tanpa ini perintah rch dan vclip tidak pernah dibuat,
# karena keduanya datang dari [project.scripts] di pyproject.toml -
# requirements.txt hanya memasang dependency, bukan project-nya.
"$PY" -m pip install -e . || \
  echo "[PERINGATAN] Gagal memasang project. Perintah rch/vclip tidak tersedia;"
echo "             cara lain: $PY -m rch --help (dari folder repo)"
echo

echo "[5/5] Membuat launcher..."
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
echo " CLI: rch --help  (atau $PY -m clipper.main)"
echo "============================================"