"""Flask web GUI — local browser interface.

Parity target: the Node.js ``web/server.js``. The same six endpoints are
exposed, with the same request/response envelopes, plus ``/api/history`` and
``/api/quit`` for the local single-user workflow.

Binds to 127.0.0.1 by default: this is a single-user desktop tool, and a
wildcard bind would expose an unauthenticated downloader to the network.
"""
from __future__ import annotations

import copy
import threading
import webbrowser
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request

from ..core.paths import default_downloads_dir

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
# Same resolution as the CLI, so the GUI and the command line agree on where
# downloads land instead of quietly writing to two different places.
DEFAULT_OUT = str(default_downloads_dir())

app = Flask(__name__)

_JOBS: Dict[int, Dict[str, Any]] = {}
_JOB_COUNTER = 0
_JOB_LOCK = threading.Lock()
_SHUTDOWN = threading.Event()
_LIVE_SERVER: Optional[Any] = None

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_host(origin: str) -> Optional[str]:
    """Extract the ``host:port`` from an Origin header, or None if unparseable."""
    parts = urlsplit(origin)
    if not parts.netloc:
        return None
    return parts.netloc.lower()


def _origin_allowed() -> bool:
    """Reject cross-origin writes, allow same-origin and non-browser clients.

    The GUI serves no authentication, so any page in the user's browser could
    otherwise POST to ``/api/quit`` or start downloads on the user's machine.
    A browser always attaches ``Origin`` to a cross-origin POST, so requiring
    it to match ``Host`` blocks that attack while leaving curl, the CLI, and
    the test-suite (which send no ``Origin``) working.

    This deliberately does not pin to a fixed allow-list: ``rch web --host``
    supports serving to other machines on the LAN, and those requests are
    same-origin from the browser that loaded them.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    origin_host = _origin_host(origin)
    request_host = (request.headers.get("Host") or "").lower()
    return origin_host is not None and origin_host == request_host


@app.before_request
def _guard_cross_origin_writes():
    if request.method not in _MUTATING_METHODS:
        return None
    if not _origin_allowed():
        return jsonify({"error": "Cross-origin request ditolak."}), 403
    return None


def _next_job_id() -> int:
    global _JOB_COUNTER
    with _JOB_LOCK:
        _JOB_COUNTER += 1
        return _JOB_COUNTER


def _row(item: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce one engine result item to the fields the UI table renders.

    ``channel-video`` items carry ``ok`` while ``channel-full`` and
    ``channel-info`` items carry ``videoOk``; both are normalised onto ``ok``
    because app.js labels every row from that single field. ``None`` in the
    text fields collapses to ``""`` so the payload is JSON-stable.

    ``status`` is cross-read from the shared ledger so a run table can also say
    where the video stands overall - downloaded, already clipped, queued - not
    just whether this one attempt succeeded.
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
        "status": _tracker_status(video_id),
    }


def _tracker_status(video_id: Any) -> Optional[str]:
    """Overall status for a video, or None when it is not in the ledger.

    Cached on ``flask.g`` so it lasts exactly one request: a channel run can be
    hundreds of rows, and folding the ledger per row would re-read the file
    hundreds of times. Outside a request context the answer is simply None.
    """
    if not video_id:
        return None
    return _tracker_statuses().get(video_id)


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
        "items": [
            _row(item)
            for item in res.get("items", [])
            if isinstance(item, dict)
        ],
    }


def _emitter(job_id: int):
    """Wire engine events into one specific job record for live progress.

    Subscribes to the events ``rch.youtube.channel`` actually emits —
    ``progress``, ``phase`` and ``video:done``. ``video:done`` carries an
    ``ok`` flag instead of arriving as separate ``item:ok``/``item:fail``
    events, so both the successful and failed rows are driven from it.
    """
    from ..core.events import create_emitter, phase_label

    def update(**fields) -> None:
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job.update(fields)

    def on_progress(payload) -> None:
        data = payload or {}
        done, total = data.get("done"), data.get("total")
        if total:
            update(progress=min(1.0, done / total), phase=f"{done}/{total}")

    def on_phase(payload) -> None:
        update(phase=phase_label(payload))

    def on_video_done(payload) -> None:
        data = payload or {}
        row = _row({"videoId": data.get("id"), "title": data.get("title", ""),
                    "ok": bool(data.get("ok")), "error": data.get("error")})
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            job["items"].append(row)

    emitter = create_emitter()
    emitter.on("progress", on_progress)
    emitter.on("phase", on_phase)
    emitter.on("video:done", on_video_done)
    return emitter


def _start_channel_job(fn, link: str, options: Dict[str, Any]) -> int:
    """Allocate a job id, bind a progress emitter to it, then start the work."""
    job_id = _next_job_id()
    with _JOB_LOCK:
        _JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "phase": "Mulai",
            "items": [],
            "result": None,
            "error": None,
        }

    emitter = _emitter(job_id)

    def finish(**fields) -> None:
        with _JOB_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job.update(fields)

    def target() -> None:
        try:
            result = fn(link, options, emitter=emitter)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            finish(status="error", progress=1, phase="Gagal", error=str(exc))
            return
        finish(status="done", progress=1, phase="Selesai", result=_summarise(result))

    threading.Thread(target=target, daemon=True).start()
    return job_id


def _body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _link() -> str:
    """Extract the requested URL, treating whitespace-only input as absent."""
    body = _body()
    return str(body.get("url") or body.get("link") or "").strip()


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    from ..youtube import thumbnail as thumb

    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400
    try:
        return jsonify(thumb.thumbnail(link))
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return jsonify({"status": False, "message": str(exc)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    from ..youtube import video as vid

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
    from ..youtube import playlist as pl

    link = _link()
    if not link:
        return jsonify({"status": False, "message": "URL wajib diisi."}), 400

    body = _body()
    try:
        result = pl.playlist_metadata(link, {"limit": pl.parse_limit(body.get("limit"))})
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return jsonify({"status": False, "message": str(exc)}), 500
    return jsonify(result), (200 if result.get("status") else 400)


@app.route("/api/channel-info", methods=["POST"])
def api_channel_info():
    from ..youtube import channel as chan

    body = _body()
    options = {
        "size": body.get("size") or "hqdefault",
        "outputDir": body.get("outputDir") or DEFAULT_OUT,
        "limit": body.get("limit") or None,
        "shorts": bool(body.get("shorts")),
    }
    job_id = _start_channel_job(chan.channel_info, _link(), options)
    return jsonify({"jobId": job_id, "status": "running"})


@app.route("/api/channel-video", methods=["POST"])
def api_channel_video():
    from ..youtube import channel as chan

    body = _body()
    options = {
        "quality": body.get("quality") or "720p",
        "outputDir": body.get("outputDir") or DEFAULT_OUT,
        "limit": body.get("limit") or None,
        "shorts": bool(body.get("shorts")),
    }
    job_id = _start_channel_job(chan.channel_video, _link(), options)
    return jsonify({"jobId": job_id, "status": "running"})


@app.route("/api/channel-full", methods=["POST"])
def api_channel_full():
    from ..youtube import channel as chan

    body = _body()
    options = {
        "size": body.get("size") or "hqdefault",
        "quality": body.get("quality") or "720p",
        "outputDir": body.get("outputDir") or DEFAULT_OUT,
        "limit": body.get("limit") or None,
        "shorts": bool(body.get("shorts")),
    }
    return jsonify({
        "jobId": _start_channel_job(chan.channel_full, _link(), options),
        "status": "running",
    })


@app.route("/api/status/<int:job_id>")
def api_status(job_id: int):
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "Job tidak ditemukan"}), 404
        snapshot = _snapshot(job)
    return jsonify(snapshot)


def _snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep, detached copy of a job record.

    The record's ``items`` list is appended to by worker threads while the
    response is serialised, so a shallow ``dict(job)`` would hand ``jsonify``
    a list that can grow mid-iteration and render a torn row. A deep copy
    freezes the state at the moment the lock is released.
    """
    return copy.deepcopy(job)


@app.route("/api/history")
def api_history():
    from ..core.report import read_history

    out = request.args.get("out") or DEFAULT_OUT
    try:
        records = read_history(out)
    except TypeError:
        return jsonify({"error": "Parameter out tidak valid"}), 400
    records.reverse()
    # Each row gains the per-video statuses it covered, so the table can show
    # "3 dari 5 sudah clip" instead of only aggregate counts. Older rows have
    # no ids= field and simply report an empty list.
    for record in records:
        ids = record.get("videoIds") or []
        statuses = _tracker_statuses()
        record["items"] = [
            {"videoId": vid, "status": statuses.get(vid)} for vid in ids
        ]
    return jsonify(records)


def _tracker_statuses() -> Dict[str, str]:
    """Map of video id to overall status, cached for this request.

    Returns an empty map outside an application context, where there is no
    request to cache against - ``_row`` is called directly by the tests and by
    ``_summarise`` outside the request cycle.
    """
    try:
        cache = getattr(g, "_tracker_statuses", None)
    except RuntimeError:
        return {}
    if cache is None:
        try:
            from ..core.tracker import statuses_for

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
    _SHUTDOWN.set()
    threading.Thread(target=_shutdown_werkzeug, daemon=True).start()
    return jsonify({"status": "stopping"})


def _shutdown_werkzeug() -> None:
    """Stop the server ``run_server`` actually started.

    The live instance is tracked in ``_LIVE_SERVER`` rather than rebuilt from
    the default host/port: rebuilding would target a different socket whenever
    the GUI was launched with ``--host``/``--port``, so ``/api/quit`` would
    never take effect.
    """
    server = _LIVE_SERVER
    if server is None:
        return
    try:
        server.shutdown()
    except Exception:  # noqa: BLE001 - best-effort local shutdown
        pass


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    from werkzeug.serving import make_server

    return make_server(host, port, app)


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
               open_browser: bool = True) -> None:
    """Run the GUI, blocking until interrupted or ``/api/quit`` is called."""
    global _LIVE_SERVER

    server = create_server(host, port)
    _LIVE_SERVER = server
    url = f"http://{host}:{port}"
    print(f"Ridikc Content Harvester GUI: {url}")
    print("Tekan Ctrl+C untuk berhenti.")
    if open_browser:
        threading.Thread(
            target=lambda: webbrowser.open(url), daemon=True
        ).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
    finally:
        server.server_close()
        _LIVE_SERVER = None
