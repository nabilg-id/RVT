"""GUI web YouTube Viral Clipper.

Alur: tempel URL → RCH mengambil metadata dan thumbnail → pilih jumlah clip,
durasi, dan gaya caption → jalankan → progres live → unduh hasil.

Pipeline pemotongan video tetap milik :mod:`clipper.services`; modul ini hanya
menyediakan antarmuka web, manajemen job, dan lapisan akuisisi dari RCH.
"""
from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Papan Video - status per video dari ledger bersama
# ---------------------------------------------------------------------------

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _status_label(status: str) -> str:
    return {
        "none": "Belum",
        "downloaded": "Sudah Download",
        "queued": "Antri",
        "processing": "Proses",
        "clipped": "Sudah Clip",
        "download_failed": "Gagal Download",
        "clip_failed": "Gagal Clip",
    }.get(status, status)


@app.route("/api/videos")
def api_videos():
    """Semua video yang pernah terlihat, beserta status download dan clip-nya.

    Disaring dan dicari di server supaya tabel tetap ringan kalau folder sudah
    berisi ratusan video.
    """
    try:
        from rch.core.tracker import list_videos, summarize
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Ledger tidak bisa dibaca: {exc}"}), 500

    status = (request.args.get("status") or "").strip()
    query = (request.args.get("q") or "").strip().lower()
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200

    rows = list_videos()
    if status and status != "all":
        rows = [r for r in rows if r["status"] == status]
    if query:
        # The id is compared case-insensitively too: video ids mix upper and
        # lower case, and a user pasting one must not have to guess the casing.
        rows = [
            r for r in rows
            if query in (r.get("title") or "").lower()
            or query in r["videoId"].lower()
        ]

    counts = summarize()
    return jsonify({
        "videos": rows[:limit],
        "total": len(rows),
        "counts": counts,
        "labels": {k: _status_label(k) for k in counts},
    })


@app.route("/api/videos/queue", methods=["POST"])
def api_queue():
    """Tandai video sebagai antri clip.

    Penanda saja, tidak memicu proses: video diclip saat tombol Generate
    ditekan, supaya tidak ada pekerjaan yang berjalan tanpa diminta.
    """
    return _set_queue(_body().get("videoId"), queued=True)


@app.route("/api/videos/unqueue", methods=["POST"])
def api_unqueue():
    return _set_queue(_body().get("videoId"), queued=False)


def _set_queue(video_id, *, queued: bool) -> tuple:
    if not isinstance(video_id, str) or not _VIDEO_ID_RE.match(video_id):
        return jsonify({"error": "videoId tidak valid."}), 400

    try:
        from rch.core.tracker import append_event, effective_status, read_state
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Ledger tidak bisa ditulis: {exc}"}), 500

    state = read_state()
    known = state.get(video_id)
    if known is None:
        # Mengantre video yang belum pernah terlihat tidak akan pernah bisa
        # dikerjakan, jadi tolak daripada membuat entri yang menggantung.
        return jsonify({"error": "Video tidak ada di daftar."}), 404

    # Mantan status queued tidak berlaku kalau klipnya sudah jalan atau selesai.
    if not queued:
        current = (known.get("clip") or {}).get("status")
        if current in ("done", "running"):
            return jsonify({
                "error": "Clip sudah berjalan atau selesai, antrean tidak berlaku.",
                "video": effective_status(known),
            }), 409

    append_event(video_id, "clip", status="queued" if queued else "none")
    return jsonify({"ok": True, "video": effective_status(read_state()[video_id])})


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


def backfill_ledger() -> int:
    """Seed the video board from clips and downloads already on disk.

    Called once when the GUI starts so a user who already has a collection sees
    it on the board immediately instead of an empty table. Idempotent, and
    wrapped because a missing or unreadable folder must not stop the server.
    """
    try:
        from rch.core.tracker import backfill_from_disk
    except Exception:  # noqa: BLE001 - tracker unavailable, board just stays empty
        return 0
    try:
        return backfill_from_disk(OUTPUT_DIR.parent / "downloads", OUTPUT_DIR)
    except Exception:  # noqa: BLE001 - never block startup on bookkeeping
        return 0


def run_server(host: str = RCH_HOST, port: int = RCH_PORT, open_browser: bool = True) -> None:
    """Jalankan GUI, memblokir sampai dihentikan atau ``/api/quit`` dipanggil."""
    global _LIVE_SERVER

    server = create_server(host, port)
    _LIVE_SERVER = server
    url = f"http://{host}:{port}"
    print(f"YouTube Viral Clipper GUI: {url}")
    print(f"Output clip: {OUTPUT_DIR}")

    added = backfill_ledger()
    if added:
        print(f"Papan video: {added} video ditemukan dari file yang sudah ada.")

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
