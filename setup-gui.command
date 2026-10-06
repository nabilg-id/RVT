#!/usr/bin/env bash
# Ridikc Video Toolkit - installer macOS / Linux (double-clickable wrapper)
#
# This file used to be a full copy of setup-gui.sh, and it drifted: it kept the
# old four-step flow and never learned about `pip install -e .`, so a mac user
# who double-clicked it ended up with every dependency installed and no `rch`
# and no `vclip` command at all - the exact failure the Windows installer was
# fixed for. Two copies of one installer will always diverge; this delegates
# instead, so there is only one to keep correct.
#
# Double-clicking a .command in Finder runs it with the working directory set
# somewhere unhelpful, hence the explicit cd before handing over.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f setup-gui.sh ]; then
  echo "[GAGAL] setup-gui.sh tidak ditemukan di folder ini."
  echo "        Pastikan file .command dan setup-gui.sh berada di folder yang sama."
  read -r -p "Tekan Enter untuk menutup..."
  exit 1
fi

# Keep the window open on failure, and on success when opened by double-click:
# a terminal that closes instantly leaves the user with nothing to read.
if bash setup-gui.sh; then
  if [ -t 0 ]; then
    read -r -p "Selesai. Tekan Enter untuk menutup..."
  fi
else
  status=$?
  echo
  echo "[GAGAL] Instalasi berhenti dengan kode $status."
  read -r -p "Tekan Enter untuk menutup..."
  exit "$status"
fi