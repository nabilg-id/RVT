"""Shared per-video ledger: what was downloaded, what was clipped, what is queued.

Both the harvester (``rch``) and the clipper (``clipper``) write here, and both
read from it, so a single file answers "which videos are done, which are not".

Why a JSONL append-only log instead of one JSON document
--------------------------------------------------------
``rch.youtube.channel`` harvests with a ``ThreadPoolExecutor`` and both GUIs may
be running at once. Read-modify-write of a single JSON file loses events in
exactly that situation. Appending a whole line in a single ``os.write`` to a
file opened ``O_APPEND`` does not, and a reader that skips unparsable lines
degrades to "missing one record" instead of "corrupt file".

A partial or interleaved line is the only failure mode worth tolerating, so
:func:`read_state` drops anything it cannot parse rather than raising.

Record shape (one JSON object per line)::

    {"v": 1, "ts": "...", "videoId": "dQw4w9WgXcQ", "event": "download",
     "status": "done", "path": "...", "title": "...", "files": []}

Events are ``seen``, ``download``, ``clip`` and ``queue``. Folding is
last-write-wins per section, so re-recording a status replaces it.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: Bumped only for an incompatible reshape; readers ignore unknown versions.
SCHEMA_VERSION = 1

TRACKER_FILENAME = "video-tracker.jsonl"

#: ``clip_<n>_<score>pts_<videoId>.mp4`` — the score is variable-width.
_CLIP_FILENAME = re.compile(
    r"^clip_\d+_\d+pts_(?P<video_id>[A-Za-z0-9_-]{11})\.mp4$"
)

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Guarded so two threads in one process cannot interleave a line's bytes.
_APPEND_LOCK = threading.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tracker_path() -> Path:
    """Where the ledger lives.

    Anchored to the repository root rather than the working directory so the
    harvester and the clipper open the same file no matter where either was
    launched from. ``VIDEO_TRACKER_FILE`` overrides it, which is how the tests
    aim it at ``tmp_path`` instead of writing into the working tree.
    """
    override = os.getenv("VIDEO_TRACKER_FILE")
    if override and override.strip():
        path = Path(override.strip()).expanduser()
        return path if path.is_absolute() else (_repo_root() / path)
    return _repo_root() / "temp" / TRACKER_FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_video_id(value: Any) -> bool:
    """True for an 11-character YouTube id.

    Used to validate anything arriving from the browser before it is written to
    the ledger: the tracker file is user-writable, so an unchecked value could
    carry a path or a newline into it.
    """
    return isinstance(value, str) and bool(_YOUTUBE_ID.match(value))


def watch_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def append_event(video_id: str, event: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Append one event. Returns the record written, or ``None`` if rejected.

    Never raises: a tracker that cannot be written must not abort a download or
    a clip job, because tracking is observability, not the product.
    """
    if not is_video_id(video_id):
        return None

    record: Dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "ts": _now(),
        "videoId": video_id,
        "event": event,
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value

    line = json.dumps(record, ensure_ascii=False) + "\n"
    if "\n" in line[:-1]:
        # json.dumps escapes newlines, so this should be unreachable; guard
        # anyway because a multi-line record would break the one-line-per-event
        # invariant everything else relies on.
        return None

    path = tracker_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _APPEND_LOCK:
            # One write of one line: atomic for O_APPEND on both POSIX and
            # Windows, which a looping write() would not be.
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except OSError:
        return None

    return record


def _fold(record: Dict[str, Any], state: Dict[str, Any]) -> None:
    video_id = record.get("videoId")
    if not is_video_id(video_id):
        return

    entry = state.setdefault(
        video_id,
        {"videoId": video_id, "title": None, "url": None,
         "download": {}, "clip": {}},
    )
    entry["updatedAt"] = record.get("ts")

    if record.get("title"):
        entry["title"] = record["title"]

    event = record.get("event")
    status = record.get("status")

    if event == "seen":
        entry.setdefault("seenAt", record.get("ts"))
        return

    if event == "download":
        entry["download"] = {
            "status": status or "done",
            "path": record.get("path"),
            "at": record.get("ts"),
        }
        if record.get("error"):
            entry["download"]["error"] = str(record["error"])[:200]
        return

    if event == "clip":
        clip = entry["clip"]
        clip["status"] = status or "done"
        clip["at"] = record.get("ts")
        if record.get("style"):
            clip["style"] = record["style"]
        if record.get("files") is not None:
            clip["files"] = list(record["files"])
        if record.get("error"):
            clip["error"] = str(record["error"])[:200]
        return


def read_state(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Fold the ledger into one record per video, keyed by video id.

    A malformed line is skipped rather than fatal: the whole point of an
    append-only log is that a torn write costs one record, not the file.
    """
    target = Path(path) if path is not None else tracker_path()
    if not target.exists():
        return {}

    state: Dict[str, Dict[str, Any]] = {}
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            _fold(record, state)

    for entry in state.values():
        entry["url"] = entry.get("url") or watch_url(entry["videoId"])
    return state


def effective_status(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse the two sections into what the dashboard actually shows.

    Returns a flat view so the browser never has to reimplement the precedence
    rules: ``queued`` only matters while the clip has not started.
    """
    download = (entry.get("download") or {}).get("status") or "none"
    clip_status = (entry.get("clip") or {}).get("status") or "none"

    if clip_status == "running":
        combined = "processing"
    elif clip_status == "done":
        combined = "clipped"
    elif clip_status == "failed":
        combined = "clip_failed"
    elif clip_status == "queued":
        combined = "queued"
    elif download == "done":
        combined = "downloaded"
    elif download == "failed":
        combined = "download_failed"
    else:
        combined = "none"

    return {
        "videoId": entry["videoId"],
        "title": entry.get("title"),
        "url": entry.get("url") or watch_url(entry["videoId"]),
        "status": combined,
        "downloadStatus": download,
        "clipStatus": clip_status,
        "clipFiles": list((entry.get("clip") or {}).get("files") or []),
        "clipStyle": (entry.get("clip") or {}).get("style"),
        "downloadPath": (entry.get("download") or {}).get("path"),
        "updatedAt": entry.get("updatedAt"),
    }


def list_videos(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every known video as a flat, browser-ready list, newest activity first."""
    views = [effective_status(e) for e in read_state(path).values()]
    views.sort(key=lambda v: (v.get("updatedAt") or ""), reverse=True)
    return views


def summarize(path: Optional[Path] = None) -> Dict[str, int]:
    """Counts per combined status, for the dashboard filter chips.

    The zero entries are pre-seeded so the frontend can render a chip with a
    count of 0 without special-casing a missing key.
    """
    counts = {
        "none": 0, "downloaded": 0, "queued": 0, "processing": 0,
        "clipped": 0, "download_failed": 0, "clip_failed": 0,
    }
    for view in list_videos(path):
        counts[view["status"]] = counts.get(view["status"], 0) + 1
    counts["total"] = sum(
        v for k, v in counts.items() if k not in ("total",)
    )
    return counts


def statuses_for(path: Optional[Path] = None) -> Dict[str, str]:
    """Map video id -> combined status, for enriching run-level tables."""
    return {v["videoId"]: v["status"] for v in list_videos(path)}


def _iter_download_folders(downloads_dir: Path) -> Iterable[tuple]:
    """Yield ``(video_id, folder)`` for folders that hold a real video file.

    ``channel-info`` produces the same folders without ``video.mp4``, so its
    presence is what distinguishes "downloaded" from "merely listed".
    """
    if not downloads_dir.is_dir():
        return
    for folder in sorted(downloads_dir.iterdir()):
        if not folder.is_dir():
            continue
        if not (folder / "video.mp4").is_file():
            continue
        video_id = _video_id_from_folder(folder)
        if video_id:
            yield video_id, folder


def _id_from_link(text: str) -> Optional[str]:
    """Pull the video id out of either link form RCH has written.

    ``https://youtu.be/<id>`` is what ``watch_url`` produces, but a hand-edited
    or older link.txt may hold a full ``watch?v=<id>&t=30s`` URL, so both are
    accepted rather than only the one the writer happens to emit today.
    """
    raw = str(text).strip()
    if not raw:
        return None

    tail = raw.rsplit("/", 1)[-1]
    if is_video_id(tail):
        return tail

    query = raw.split("?", 1)[1] if "?" in raw else ""
    for part in query.split("&"):
        if part.startswith("v=") and is_video_id(part[2:]):
            return part[2:]

    tail = tail.split("?", 1)[0]
    return tail if is_video_id(tail) else None


def _video_id_from_folder(folder: Path) -> Optional[str]:
    """Read the id from ``metadata.json``, or fall back to the legacy
    ``link.txt``. Both layouts are supported on purpose: this is about to be
    removed and existing harvests still have the old one.
    """
    meta = folder / "metadata.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            candidate = data.get("id") or data.get("videoId")
            if is_video_id(candidate):
                return candidate
        except (OSError, ValueError):
            pass

    link = folder / "link.txt"
    if link.is_file():
        try:
            return _id_from_link(link.read_text(encoding="utf-8"))
        except OSError:
            return None
    return None


def backfill_from_disk(
    downloads_dir: Optional[Path] = None,
    clips_dir: Optional[Path] = None,
) -> int:
    """Seed the ledger from files already on disk. Returns videos recorded.

    Idempotent, and additive per section rather than per video: a video already
    known from a download still gets its existing clips recorded, because
    "downloaded" on the board while the MP4s sit in clips/ would be a lie.
    What it will not do is rewind a status that is already recorded.
    """
    state = read_state()
    added = 0

    if downloads_dir is not None:
        for video_id, folder in _iter_download_folders(Path(downloads_dir)):
            recorded = (state.get(video_id, {}).get("download") or {}).get("status")
            if recorded in ("done", "failed"):
                continue
            title = None
            meta = folder / "metadata.json"
            if meta.is_file():
                try:
                    title = json.loads(meta.read_text(encoding="utf-8")).get("title")
                except (OSError, ValueError):
                    title = None
            if append_event(video_id, "download", status="done",
                            path=str(folder / "video.mp4"), title=title,
                            source="backfill"):
                added += 1

    if clips_dir is not None:
        clips_path = Path(clips_dir)
        if clips_path.is_dir():
            for clip in sorted(clips_path.glob("clip_*.mp4")):
                match = _CLIP_FILENAME.match(clip.name)
                if not match:
                    continue
                video_id = match.group("video_id")
                already = (state.get(video_id, {}).get("clip") or {}).get("files")
                if already:
                    continue
                if append_event(video_id, "clip", status="done",
                                files=[clip.name], source="backfill"):
                    added += 1

    return added


def compact(path: Optional[Path] = None) -> int:
    """Rewrite the ledger as one folded record per video.

    Off the default path — the log only grows by ~200 bytes per event, so this
    is for a user who has run thousands of jobs and wants the file small again.
    """
    target = Path(path) if path is not None else tracker_path()
    if not target.exists():
        return 0

    state = read_state(target)
    lines = []
    for video_id in sorted(state):
        entry = state[video_id]
        for event, section in (("download", "download"), ("clip", "clip")):
            data = entry.get(section) or {}
            if not data:
                continue
            record = {
                "v": SCHEMA_VERSION,
                "ts": data.get("at") or entry.get("updatedAt") or _now(),
                "videoId": video_id,
                "event": event,
                "status": data.get("status"),
            }
            if entry.get("title"):
                record["title"] = entry["title"]
            for key in ("path", "style", "files", "error"):
                if data.get(key) is not None:
                    record[key] = data[key]
            lines.append(json.dumps(record, ensure_ascii=False))

    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""),
                       encoding="utf-8", newline="\n")
        tmp.replace(target)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return 0
    return len(state)