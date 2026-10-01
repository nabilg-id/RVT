#!/usr/bin/env bash
# Ridikc Content Harvester - launcher macOS
# Dipakai sebagai shortcut desktop: dobel-klik file ini di Finder.
cd "$(dirname "$0")/.." || exit 1
exec bash launchers/rch-gui.sh
