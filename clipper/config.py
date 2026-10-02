"""Konfigurasi YouTube Viral Clipper.

Semua path dihitung dari lokasi paket, bukan dari direktori kerja. Sebelumnya
nilai-nilai ini memakai path relatif seperti ``./clips``, yang membuat
aplikasi hanya bisa dijalankan dari root proyek; sekarang GUI web, CLI, dan
pytest bisa dipanggil dari mana saja.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(PACKAGE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", REPO_ROOT / "clips"))
TEMP_DIR = Path(os.getenv("TEMP_DIR", REPO_ROOT / "temp"))
COOKIES_FILE = Path(os.getenv("COOKIES_FILE", REPO_ROOT / "cookies.txt"))
ASSET_DIR = Path(os.getenv("ASSET_DIR", REPO_ROOT / "asset"))
TRANSITION_FILE = ASSET_DIR / "transisi.mp4"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE") or None

YOUTUBE_COOKIES_CONTENT = os.getenv("YOUTUBE_COOKIES_CONTENT")
YOUTUBE_COOKIES_BROWSER = os.getenv("YOUTUBE_COOKIES_BROWSER")
YOUTUBE_USER_AGENT = os.getenv("YOUTUBE_USER_AGENT")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free")

RCH_HOST = os.getenv("RCH_HOST", "127.0.0.1")
RCH_PORT = int(os.getenv("RCH_PORT", "8787"))
RCH_HISTORY_LIMIT = int(os.getenv("RCH_HISTORY_LIMIT", "20"))

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
