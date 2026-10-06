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

from rch.core.paths import default_clip_output_dir, ensure_dir

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


def _env_str(name: str, default: str = "") -> str:
    """Baca variabel lingkungan tanpa spasi di kedua ujung.

    Spasi flanking muncul dari hal yang sangat biasa: ``set VAR=tiny && next``
    di Windows cmd menyisipkan spasi sebelum ``&&``, dan menyalin baris dari
    chat atau editor sering membawa spasi ikut. Nilai seperti ``"medium "``
    lalu diteruskan apa adanya ke faster-whisper, yang menolak dengan
    "Invalid model size" - pesan yang tidak menyepoint ke penyebabnya.

    Nilai kosong atau berisi spasi saja diperlakukan sebagai tidak di-set, jadi
    ``WHISPER_MODEL=`` di .env jatuh ke default dan bukan ke string kosong yang
    akan ditolak lebih hilir.
    """
    value = (os.getenv(name) or "").strip()
    return value if value else default


def _env_opt(name: str):
    """Seperti :func:`_env_str`, tapi string kosong menjadi ``None``."""
    value = _env_str(name)
    return value or None


def _env_path(name: str, default: Path) -> Path:
    """Baca path dari environment, selalu mutlak.

    Path relatif di-anchor ke repo root, bukan ke direktori kerja. Dua alasan:

    - Menjalankan aplikasi dari folder lain harus tetap menemukan folder yang
      sama; relatif terhadap CWD membuatnya menulis dan membaca dari tempat
      yang berbeda tergantung dari mana perintah dijalankan.
    - Flask ``send_from_directory`` selalu menghitung
      ``os.path.join(app.root_path, path)``. ``app.root_path`` adalah folder
      paket ``clipper``, jadi path relatif seperti ``clips`` akan dicari di
      ``clipper/clips`` dan route download membalas 404 padahal file-nya ada.
    """
    raw = _env_str(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


GEMINI_API_KEY = _env_str("GEMINI_API_KEY")
# ensure_dir, not a bare mkdir. This runs at import, and the default output now
# lives in the OS Downloads folder rather than inside the repo, so it can fail
# for reasons the user cannot do anything about: OneDrive not signed in, an ACL
# that denies writing, a full disk. Unguarded, that exception did not fail one
# download - it killed this module, and with it both the GUI and the CLI, each
# on a traceback. ensure_dir falls back to a folder inside the repo so the app
# still starts. The move is announced, because silently writing somewhere else
# is its own kind of confusing.
_requested_output = _env_path("OUTPUT_DIR", default_clip_output_dir())
_requested_temp = _env_path("TEMP_DIR", REPO_ROOT / "temp")
OUTPUT_DIR = ensure_dir(_requested_output)
TEMP_DIR = ensure_dir(_requested_temp)
if OUTPUT_DIR != _requested_output:
    print(f"⚠️ Folder output '{_requested_output}' tidak bisa dibuat. "
          f"Menulis ke '{OUTPUT_DIR}'.")
if TEMP_DIR != _requested_temp:
    print(f"⚠️ Folder sementara '{_requested_temp}' tidak bisa dibuat. "
          f"Menulis ke '{TEMP_DIR}'.")
COOKIES_FILE = _env_path("COOKIES_FILE", REPO_ROOT / "cookies.txt")
ASSET_DIR = _env_path("ASSET_DIR", REPO_ROOT / "asset")
TRANSITION_FILE = ASSET_DIR / "transisi.mp4"

WHISPER_MODEL = _env_str("WHISPER_MODEL", "medium")
WHISPER_LANGUAGE = _env_opt("WHISPER_LANGUAGE")

YOUTUBE_COOKIES_CONTENT = _env_opt("YOUTUBE_COOKIES_CONTENT")
YOUTUBE_COOKIES_BROWSER = _env_opt("YOUTUBE_COOKIES_BROWSER")
YOUTUBE_USER_AGENT = _env_opt("YOUTUBE_USER_AGENT")

OPENROUTER_API_KEY = _env_opt("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _env_str("OPENROUTER_MODEL", "openrouter/free")

RCH_HOST = _env_str("RCH_HOST", "127.0.0.1")
RCH_PORT = int(_env_str("RCH_PORT", "8787"))
RCH_HISTORY_LIMIT = int(_env_str("RCH_HISTORY_LIMIT", "20"))
