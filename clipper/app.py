"""GUI web YouTube Viral Clipper.

Alur: tempel URL → RCH mengambil metadata dan thumbnail → pilih jumlah clip,
durasi, dan gaya caption → jalankan → progres live → unduh hasil.

Pipeline pemotongan video tetap milik :mod:`clipper.services`; modul ini hanya
menyediakan antarmuka web, manajemen job, dan lapisan akuisisi dari RCH.
"""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_from_directory

from .config import (
    OUTPUT_DIR,
    RCH_HOST,
    RCH_PORT,
    TEMP_DIR,
)
from .progress import Job, StreamCapture
from .styles.caption_styles import CAPTION_STYLES

app = Flask(__name__, template_folder="templates", static_folder="static")

_JOBS: Dict[int, Job] = {}
_JOB_COUNTER = 0
_JOB_LOCK = threading.Lock()
_LIVE_SERVER: Optional[Any] = None

_MAX_LOG = 200
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEFAULT_CAPTION_STYLE = "clean_white"
DEFAULT_NUM_CLIPS = 3
DEFAULT_MIN_DURATION = 20
DEFAULT_MAX_DURATION = 60
MIN_CLIPS, MAX_CLIPS = 1, 20
MIN_SECONDS, MAX_SECONDS = 5, 600

_RCH_HISTORY_FILE = "clip-history.log"


# ---------------------------------------------------------------------------
# Keamanan: tolak tulis lintas asal
# ---------------------------------------------------------------------------

def _origin_host(origin: str) -> Optional[str]:
    parts = urlsplit(origin)
    return parts.netloc.lower() or None


@app.before_request
def _guard_cross_origin_writes():
    """GUI tidak punya autentikasi, jadi halaman mana pun bisa mencoba POST.

    Browser selalu mengirim ``Origin`` untuk POST lintas asal, jadi
    mewajibkannya cocok dengan ``Host`` menutup celah itu tanpa merusak
    klien non-browser seperti curl atau test suite.
    """
    if request.method not in _MUTATING:
        return None
    origin = request.headers.get("Origin")
    if not origin:
        return None
    if origin.lower() == "null":
        return jsonify({"error": "Cross-origin request ditolak."}), 403
    if _origin_host(origin) != (request.headers.get("Host") or "").lower():
        return jsonify({"error": "Cross-origin request ditolak."}), 403
    return None


# ---------------------------------------------------------------------------
# Validasi input
# ---------------------------------------------------------------------------

def _is_youtube_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.netloc or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _validate_clip_request(body: Dict[str, Any]) -> tuple:
    """Kembalikan ``(payload, error)``; ``error`` berupa pesan 400 bila gagal."""
    url = (body.get("url") or "").strip()
    if not url:
        return None, "URL wajib diisi."
    if not _is_youtube_url(url):
        return None, "URL bukan tautan YouTube yang valid."

    style = body.get("style") or DEFAULT_CAPTION_STYLE
    if style not in CAPTION_STYLES:
        return None, f"Gaya caption tidak dikenal: {style}"

    num_clips = _clamp_int(body.get("numClips"), MIN_CLIPS, MAX_CLIPS, DEFAULT_NUM_CLIPS)
    min_dur = _clamp_int(body.get("minDur"), MIN_SECONDS, MAX_SECONDS, DEFAULT_MIN_DURATION)
    max_dur = _clamp_int(body.get("maxDur"), MIN_SECONDS, MAX_SECONDS, DEFAULT_MAX_DURATION)
    if min_dur >= max_dur:
        return None, "Durasi minimum harus lebih kecil dari durasi maksimum."

    return {
        "url": url,
        "style": style,
        "numClips": num_clips,
        "minDur": min_dur,
        "maxDur": max_dur,
    }, None


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

def _next_job_id() -> int:
    global _JOB_COUNTER
    with _JOB_LOCK:
        _JOB_COUNTER += 1
        return _JOB_COUNTER


def _clip_video_id(payload: dict) -> str | None:
    """The source video id for a clip job, or None if the URL is unusable."""
    try:
        from rch.youtube.common import extract_video_id

        return extract_video_id(payload.get("url") or "")
    except Exception:  # noqa: BLE001 - tracking must never block a job
        return None


def _track(event: str, payload: dict, **fields) -> None:
    """Append one clip event to the shared ledger, ignoring failures.

    The ledger is observability: a clip must still run and still produce files
    if it cannot be recorded.
    """
    video_id = _clip_video_id(payload)
    if not video_id:
        return
    try:
        from rch.core.tracker import append_event

        append_event(video_id, event, **fields)
    except Exception:  # noqa: BLE001 - tracking must never break a clip job
        pass


def _track_clip_start(job: Job, payload: dict) -> None:
    _track("clip", payload, status="running", style=payload.get("style"))
    job.video_id = _clip_video_id(payload)


def _run_clip_job(job: Job, payload: dict) -> None:
    """Jalankan pipeline di thread terpisah sambil menangkap log-nya."""
    try:
        from .services.video_processor import VideoProcessor

        processor = VideoProcessor(caption_style=payload["style"])
        job.append_log(f"🎨 Gaya caption: {processor.caption_maker.styles[payload['style']]['name']}")
        with StreamCapture(job.append_log):
            outputs, title = processor.process_video(
                payload["url"],
                payload["numClips"],
                payload["minDur"],
                payload["maxDur"],
            )
        job.set_title(title)
        with job._lock:
            job.outputs = [Path(o).name for o in outputs]
        job.finish()
        if outputs:
            _track("clip", payload, status="done", style=payload["style"],
                   files=[Path(o).name for o in outputs], title=title)
        else:
            _track("clip", payload, status="failed", style=payload["style"],
                   error="Tidak ada clip yang berhasil dibuat")
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        job.finish(error=str(exc))
        _track("clip", payload, status="failed", style=payload["style"],
               error=str(exc))


def _append_history(payload: dict, job: Job) -> None:
    """Catat satu baris riwayat clip di ``clip-history.log``.

    ``videoId`` ditulis sebagai field terakhir supaya baris lama yang hanya
    punya tujuh field tetap terbaca oleh :func:`read_history`.
    """
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        stamp,
        payload["style"],
        f"clips={len(job.outputs)}",
        f"min={payload['minDur']}",
        f"max={payload['maxDur']}",
        job.status,
        (job.title or "-").replace("|", "/")[:60],
    ]
    video_id = getattr(job, "video_id", None) or _clip_video_id(payload)
    parts.append(f"id={video_id}" if video_id else "id=-")
    line = " | ".join(parts)
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(TEMP_DIR / _RCH_HISTORY_FILE, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def read_history(limit: int = 20) -> list:
    """Baca riwayat clip, terbaru lebih dulu.

    Baris lama punya tujuh field dan tidak menyebut video id; field kedelapan
    ``id=`` hanya ada pada run yang lebih baru dan dibaca kalau ada.
    """
    path = TEMP_DIR / _RCH_HISTORY_FILE
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        row = {
            "timestamp": parts[0],
            "style": parts[1],
            "clips": _strip_prefix(parts[2], "clips="),
            "range": f"{_strip_prefix(parts[3], 'min=')}–{_strip_prefix(parts[4], 'max=')}s",
            "status": parts[5],
            "title": parts[6],
            "videoId": None,
        }
        if len(parts) > 7:
            raw = _strip_prefix(parts[7], "id=")
            if raw and raw != "-":
                row["videoId"] = raw
        rows.append(row)
    rows.reverse()
    return rows[:limit]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    return render_template(
        "index.html",
        styles=[{"key": k, "name": v["name"]} for k, v in CAPTION_STYLES.items()],
        output_dir=str(OUTPUT_DIR),
        defaults={
            "numClips": DEFAULT_NUM_CLIPS,
            "minDur": DEFAULT_MIN_DURATION,
            "maxDur": DEFAULT_MAX_DURATION,
            "style": DEFAULT_CAPTION_STYLE,
        },
    )


@app.route("/api/styles")
def api_styles():
    return jsonify([{"key": k, "name": v["name"]} for k, v in CAPTION_STYLES.items()])


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Ambil metadata + thumbnail sebelum menjalankan clip."""
    from rch.youtube.metadata import get_video_info
    from rch.youtube.thumbnail import thumbnail

    body = _body()
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL wajib diisi."}), 400
    if not _is_youtube_url(url):
        return jsonify({"error": "URL bukan tautan YouTube yang valid."}), 400

    preview = {"url": url}
    try:
        from rch.youtube.common import extract_video_id

        video_id = extract_video_id(url)
        if video_id:
            resolved = thumbnail(url)
            preview.update({
                "id": video_id,
                "title": resolved["result"]["title"],
                "thumbnail": resolved["result"]["thumbnails"].get("maxresdefault"),
            })
            info = get_video_info(video_id)
            if info:
                preview["duration"] = info.get("duration")
                preview["uploadDate"] = info.get("uploadDate")
    except Exception as exc:  # noqa: BLE001 - preview is best-effort
        preview["warning"] = str(exc)

    return jsonify(preview)


@app.route("/api/clip", methods=["POST"])
def api_clip():
    payload, error = _validate_clip_request(_body())
    if error:
        return jsonify({"error": error}), 400

    job = Job(_next_job_id(), payload)
    with _JOB_LOCK:
        _JOBS[job.id] = job

    _track_clip_start(job, payload)

    threading.Thread(target=_run_clip_job, args=(job, payload), daemon=True).start()
    return jsonify({"jobId": job.id, "status": "running"})


@app.route("/api/status/<int:job_id>")
def api_status(job_id: int):
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    snapshot = job.snapshot(log_tail=_MAX_LOG)
    if snapshot["status"] != "running" and job.claim_history():
        # The browser polls until the job settles, so without this guard every
        # poll would append another line to the history file.
        _append_history(job.request, job)
    return jsonify(snapshot)


@app.route("/api/history")
def api_history():
    return jsonify(read_history())


@app.route("/clips/<path:name>")
def serve_clip(name: str):
    return send_from_directory(str(OUTPUT_DIR), name, conditional=True)


@app.route("/api/quit", methods=["POST"])
def api_quit():
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"status": "stopping"})


def _shutdown() -> None:
    server = _LIVE_SERVER
    if server is not None:
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 - best-effort local shutdown
            pass


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def create_server(host: str = RCH_HOST, port: int = RCH_PORT):
    from werkzeug.serving import make_server

    return make_server(host, port, app)


def run_server(host: str = RCH_HOST, port: int = RCH_PORT, open_browser: bool = True) -> None:
    """Jalankan GUI, memblokir sampai dihentikan atau ``/api/quit`` dipanggil."""
    global _LIVE_SERVER

    server = create_server(host, port)
    _LIVE_SERVER = server
    url = f"http://{host}:{port}"
    print(f"YouTube Viral Clipper GUI: {url}")
    print(f"Output clip: {OUTPUT_DIR}")
    print("Tekan Ctrl+C untuk berhenti.")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
    finally:
        server.server_close()
        _LIVE_SERVER = None


if __name__ == "__main__":
    run_server()
