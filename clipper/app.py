"""GUI web YouTube Viral Clipper.

Alur: tempel URL → RCH mengambil metadata dan thumbnail → pilih jumlah clip,
durasi, dan gaya caption → jalankan → progres live → unduh hasil.

Pipeline pemotongan video tetap milik :mod:`clipper.services`; modul ini hanya
menyediakan antarmuka web, manajemen job, dan lapisan akuisisi dari RCH.
"""
from __future__ import annotations

import json
import re
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request, send_from_directory

from rch.core.jobs import append_item as jobs_append
from rch.core.jobs import create_job as jobs_create
from rch.core.jobs import detach as jobs_detach
from rch.core.jobs import prune as jobs_prune
from rch.core.jobs import update as jobs_update
from rch.core.paths import default_downloads_dir

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

_RCH_HISTORY_FILE = "clip-history.jsonl"

#: Still read, never written. The old line format could not hold a title with a
#: pipe or a newline, so the writer rewrote pipes to slashes and cut titles at
#: sixty characters. See ``_append_history``.
_LEGACY_HISTORY_FILE = "clip-history.log"

#: Legacy lines have seven fields; an eighth carries the video id.
_LEGACY_MIN_FIELDS = 7


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


# ---------------------------------------------------------------------------
# Job download (channel / playlist / single video)
#
# Two kinds of job share one registry and one id counter. They used to live in
# separate apps, each with its own _JOBS and its own counter starting at zero,
# so both handed out job id 1 and both served /api/status/<id>. Merging the
# routes without merging the registry would have had the download page reading
# the clipper's job 1 and vice versa. One counter is what makes an id mean one
# job.
# ---------------------------------------------------------------------------

def _start_download_job(command: str, **fields) -> int:
    """Register a download job and return its id. No work is started here."""
    job_id = _next_job_id()
    with _JOB_LOCK:
        jobs_create(_JOBS, job_id, kind="download", command=command, **fields)
    return job_id


def _update_download_job(job_id: int, **fields) -> None:
    """Merge fields into a download job, then make room in the registry."""
    with _JOB_LOCK:
        jobs_update(_JOBS, job_id, **fields)
        # The registry is otherwise unbounded, and each record holds every
        # per-video row it collected.
        jobs_prune(_JOBS)


def _append_download_item(job_id: int, row: dict) -> None:
    with _JOB_LOCK:
        jobs_append(_JOBS, job_id, row)


def _job_snapshot(job) -> dict:
    """Serialise either kind of job.

    A clip job is a Job object with its own lock and snapshot; a download job is
    a plain record in the shared registry. Both answers carry status, progress
    and phase because both UIs read those three.
    """
    if isinstance(job, Job):
        return job.snapshot(log_tail=_MAX_LOG)
    return jobs_detach(job)


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
    """Jalankan pipeline di thread terpisah sambil menangkap log-nya.

    The shared ledger is written *before* the job status changes, never after.
    Job status is what every client polls, so it has to be the last thing to
    move: finishing first let a poller see a settled job while the video board
    still showed the video as processing, and the two disagreed until the next
    event arrived.
    """
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
        if outputs:
            _track("clip", payload, status="done", style=payload["style"],
                   files=[Path(o).name for o in outputs], title=title)
        else:
            _track("clip", payload, status="failed", style=payload["style"],
                   error="Tidak ada clip yang berhasil dibuat")
        job.finish()
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        _track("clip", payload, status="failed", style=payload["style"],
               error=str(exc))
        job.finish(error=str(exc))


def _append_history(payload: dict, job: Job) -> None:
    """Catat satu run clip di ``clip-history.jsonl``.

    JSON Lines, satu objek per baris, ditambahkan tanpa menulis ulang berkas -
    append tidak boleh berarti rewrite, karena run yang terputus di tengah akan
    mengorhankan seluruh riwayat.

    Format lama menyimpan judul sebagai field teks di dalam baris pipe-delimited,
    sehingga judul yang mengandung pipe harus ditulis ulang jadi garis miring dan
    judul panjang dipotong enam puluh karakter. Field yang dipakai orang untuk
    mengenali sebuah clip justru yang paling liable dirusak.sekarang tersimpan
    apa adanya; tidak ada lagi yang perlu di-escape.
    """
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        "timestamp": stamp,
        "style": payload.get("style"),
        "clips": len(job.outputs or []),
        "minDur": payload.get("minDur"),
        "maxDur": payload.get("maxDur"),
        "status": job.status,
        "title": job.title,
        "videoId": getattr(job, "video_id", None) or _clip_video_id(payload),
        "files": list(job.outputs or []),
    }
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(TEMP_DIR / _RCH_HISTORY_FILE, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Riwayat adalah kenyamanan. Kehilangan satu baris tidak boleh costing
        # clip milik pengguna, jadi penulisan ini sengaja best-effort.
        pass


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def _to_int(value) -> int:
    """Coerce a count that may have been stored as text.

    The legacy line format stored ``clips=2``, so records read from it arrive as
    strings. Normalising here means the API hands the table a number either way
    instead of the caller having to know which file a row came from.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _history_view(record: dict) -> dict:
    """Project a stored record onto the shape ``app.js`` renders.

    ``range`` stays a formatted string because it is display text, but the
    underlying numbers are real ones rather than the digits scraped out of
    ``min=20``.
    """
    low = record.get("minDur")
    high = record.get("maxDur")
    video_id = record.get("videoId")
    return {
        "timestamp": record.get("timestamp"),
        "style": record.get("style"),
        "clips": _to_int(record.get("clips")),
        "range": f"{low}–{high}s",
        "status": record.get("status"),
        "title": record.get("title"),
        "videoId": video_id if video_id else None,
    }


def _parse_legacy_line(line: str) -> Optional[dict]:
    """Read one legacy ``clip-history.log`` line into a stored-shape record."""
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < _LEGACY_MIN_FIELDS:
        return None
    record = {
        "timestamp": parts[0],
        "style": parts[1],
        "clips": _strip_prefix(parts[2], "clips="),
        "minDur": _strip_prefix(parts[3], "min="),
        "maxDur": _strip_prefix(parts[4], "max="),
        "status": parts[5],
        "title": parts[6],
        "videoId": None,
    }
    if len(parts) > _LEGACY_MIN_FIELDS:
        raw = _strip_prefix(parts[7], "id=")
        if raw and raw != "-":
            record["videoId"] = raw
    return record


def read_history(limit: int = 20) -> list:
    """Baca riwayat clip, terbaru lebih dulu.

    Dua berkas dibaca: ``history.jsonl`` yang sekarang dan ``clip-history.log``
    yang lama. Yang lama dibaca lebih dulu karena isinya lebih tua, jadi folder
    yang sudah punya riwayat tidak tampak kosong begitu format baru mulai
    ditulis di sana.
    """
    rows = []

    legacy = TEMP_DIR / _LEGACY_HISTORY_FILE
    if legacy.exists():
        try:
            for line in legacy.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = _parse_legacy_line(line)
                if record is not None:
                    rows.append(_history_view(record))
        except OSError:
            pass

    current = TEMP_DIR / _RCH_HISTORY_FILE
    if current.exists():
        try:
            text = current.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # Baris terpotong di tengah adalah apa yang tersisa dari run
                # yang dibunuh, jadi ini diharapkan dan tidak boleh menutupi
                # entri di sekitarnya.
                continue
            if isinstance(record, dict):
                rows.append(_history_view(record))

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


@app.route("/download")
def download_page() -> str:
    """The harvester's page, kept as it was rather than merged into the markup.

    It is a working interface for a different job - downloading rather than
    clipping - so rewriting it into the clipper's layout would have cost more
    than it gained. Only its asset names and the one API path it calls had to
    change.
    """
    return render_template("download.html", output_dir=DEFAULT_OUT)


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
        snapshot = _job_snapshot(job)
        # Only a clip job has history to write, and only once. The browser polls
        # until a job settles, so without this guard every poll would append
        # another line.
        is_clip = isinstance(job, Job)
        if is_clip and snapshot["status"] != "running" and job.claim_history():
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


# ---------------------------------------------------------------------------
# Download routes
#
# Moved here from the harvester's own Flask app so one process serves the whole
# tool. They share this app's job registry and /api/status, which is what makes
# a job id mean one job across both halves.
# ---------------------------------------------------------------------------

DEFAULT_OUT = str(default_downloads_dir())


def _body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _link() -> str:
    """The requested URL, treating whitespace-only input as absent."""
    raw = _body().get("url") or _body().get("link") or ""
    return str(raw).strip()


def _result_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce one engine result item to the fields the status table renders.

    ``channel-video`` items carry ``ok`` while ``channel-full`` and
    ``channel-info`` items carry ``videoOk``; both land on ``ok`` because the
    table labels every row from that one field.
    """
    ok = item.get("ok")
    if ok is None:
        ok = item.get("videoOk")
    video_id = item.get("videoId") or item.get("id")
    return {
        "ok": ok,
        "videoId": video_id,
        "title": item.get("title") or "",
        "error": item.get("error") or "",
    }


def _summarise(result: Any) -> Any:
    """Reduce a full engine result to the fields the status table renders."""
    if not isinstance(result, dict):
        return result
    if not result.get("status"):
        return {"message": result.get("message", "Gagal")}
    res = result.get("result", {})
    if not isinstance(res, dict):
        return res
    return {
        "total": res.get("total"),
        "success": res.get("success"),
        "failed": res.get("failed"),
        "unavailable": res.get("unavailable"),
        "zipPath": res.get("zipPath"),
        "items": [_result_row(item) for item in res.get("items", [])
                  if isinstance(item, dict)],
    }


def _download_emitter(job_id: int):
    """Wire engine events into one download job for live progress."""
    from rch.core.events import create_emitter, phase_label

    def update(**fields) -> None:
        with _JOB_LOCK:
            jobs_update(_JOBS, job_id, **fields)

    def on_progress(payload) -> None:
        data = payload or {}
        done, total = data.get("done"), data.get("total")
        if total:
            update(progress=min(1.0, done / total), phase=f"{done}/{total}")

    def on_phase(payload) -> None:
        update(phase=phase_label(payload))

    def on_video_done(payload) -> None:
        data = payload or {}
        _append_download_item(job_id, _result_row({
            "videoId": data.get("id"),
            "title": data.get("title", ""),
            "ok": bool(data.get("ok")),
            "error": data.get("error"),
        }))

    emitter = create_emitter()
    emitter.on("progress", on_progress)
    emitter.on("phase", on_phase)
    emitter.on("video:done", on_video_done)
    return emitter


def _start_download(fn, command: str, link: str,
                    options: Dict[str, Any]) -> int:
    """Allocate a job, bind its emitter, and run ``fn`` on a worker thread.

    ``command`` is passed in rather than read off ``fn`` because the label is
    what the run history shows - ``channel-full``, not ``channel_full`` - and
    because a test that stubs the engine should not change what the job calls
    itself.
    """
    job_id = _start_download_job(command)
    emitter = _download_emitter(job_id)

    def finish(**fields) -> None:
        _update_download_job(job_id, **fields)

    def target() -> None:
        try:
            result = fn(link, options, emitter=emitter)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            finish(status="error", progress=1, phase="Gagal", error=str(exc))
            return
        finish(status="done", progress=1, phase="Selesai",
               result=_summarise(result))

    threading.Thread(target=target, daemon=True).start()
    return job_id


@app.route("/api/info", methods=["POST"])
def api_info():
    from rch.youtube import thumbnail as thumb

    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400
    try:
        return jsonify(thumb.thumbnail(link))
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return jsonify({"status": False, "message": str(exc)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    from rch.youtube import video as vid

    body = _body()
    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400
    result = vid.download(link, {
        "format": body.get("format") or "mp4",
        "quality": body.get("quality") or "720p",
        "outputDir": body.get("outputDir") or DEFAULT_OUT,
    })
    return jsonify(result), (200 if result.get("status") else 400)


@app.route("/api/playlist", methods=["POST"])
def api_playlist():
    from rch.youtube import playlist as pl

    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400
    body = _body()
    try:
        result = pl.playlist_metadata(
            link, {"limit": pl.parse_limit(body.get("limit"))})
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return jsonify({"status": False, "message": str(exc)}), 500
    return jsonify(result), (200 if result.get("status") else 400)


def _channel_options(body: Dict[str, Any], *, with_size: bool) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "quality": body.get("quality") or "720p",
        "outputDir": body.get("outputDir") or DEFAULT_OUT,
        "limit": body.get("limit") or None,
        "shorts": bool(body.get("shorts")),
    }
    if with_size:
        options["size"] = body.get("size") or "hqdefault"
    return options


def _channel_route(fn_name: str, command: str, *, with_size: bool):
    from rch.youtube import channel as chan

    body = _body()
    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400
    job_id = _start_download(getattr(chan, fn_name), command, link,
                             _channel_options(body, with_size=with_size))
    return jsonify({"jobId": job_id, "status": "running"})


@app.route("/api/channel-info", methods=["POST"])
def api_channel_info():
    return _channel_route("channel_info", "channel-info", with_size=True)


@app.route("/api/channel-video", methods=["POST"])
def api_channel_video():
    return _channel_route("channel_video", "channel-video", with_size=False)


@app.route("/api/channel-full", methods=["POST"])
def api_channel_full():
    return _channel_route("channel_full", "channel-full", with_size=True)


@app.route("/api/runs")
def api_runs():
    """Harvest run history.

    Named /api/runs because /api/history was already the clipper's clip
    history, and one path cannot mean two different lists. The older app served
    harvest history from /api/history, which is why the name changed rather than
    overwriting the clip history that was already there.
    """
    from rch.core.report import read_history

    out = request.args.get("out") or DEFAULT_OUT
    try:
        records = read_history(out)
    except TypeError:
        return jsonify({"error": "Parameter out tidak valid"}), 400
    records.reverse()
    # Each row gains the per-video statuses it covered, so the table can say
    # "3 dari 5 sudah clip" rather than only aggregate counts. A legacy row whose
    # ids were truncated reports only those, and one with no ids reports none.
    for record in records:
        ids = record.get("videoIds") or []
        statuses = _tracker_statuses()
        record["items"] = [
            {"videoId": vid, "status": statuses.get(vid)} for vid in ids
        ]
    return jsonify(records)


def _tracker_statuses() -> Dict[str, str]:
    """Map of video id to overall status, cached for this request.

    A channel run is hundreds of rows and folding the ledger per row would
    re-read the file hundreds of times. Outside a request context there is no
    request to cache against, so the answer is simply empty.
    """
    try:
        cache = getattr(g, "_tracker_statuses", None)
    except RuntimeError:
        return {}
    if cache is None:
        try:
            from rch.core.tracker import statuses_for

            cache = statuses_for()
        except Exception:  # noqa: BLE001 - a missing ledger must not break history
            cache = {}
        try:
            g._tracker_statuses = cache
        except RuntimeError:
            return cache
    return cache


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

    The downloads folder is the same one the harvester writes to, resolved by the
    shared helper. It used to be derived from ``OUTPUT_DIR.parent / "downloads"``,
    which pointed at a ``downloads`` folder next to the clip output and so read
    nothing at all once downloads moved to the native Downloads folder - the
    board silently started empty for every existing user.
    """
    try:
        from rch.core.tracker import backfill_from_disk
    except Exception:  # noqa: BLE001 - tracker unavailable, board just stays empty
        return 0
    try:
        return backfill_from_disk(Path(default_downloads_dir()), OUTPUT_DIR)
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
