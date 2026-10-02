"""YouTube channel-scoped harvesting (Python rewrite).

Ported from the Node.js ``lib/youtube/channelFull.js`` (modes ``channel-info``
and ``channel-full``) and the Node.js ``lib/youtube/channelVideo.js`` (mode
``channel-video``). Every external boundary — channel enumeration, metadata
lookup, per-video download, thumbnail HTTP fetch, and sleep — is a keyword-only
injectable so the orchestration is testable without network or subprocesses.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..core.checkpoint import clear_checkpoint, load_checkpoint, mark_completed, save_checkpoint
from ..core.events import create_emitter
from ..core.zip_util import ZipStream
from .common import slugify

_DEFAULT_OUTPUT_DIR = "./downloads"
_DEFAULT_SIZE = "hqdefault"
_DEFAULT_QUALITY = "720p"
_DEFAULT_CONCURRENCY = 2
_DEFAULT_SUB_LANG = "all"
_VIDEO_FILENAME = "video"
_VIDEO_EXTENSION = "mp4"
_VIDEO_ARCNAME = "video.mp4"
_SHORTS_DIR_SUFFIX = "-shorts"
_THUMB_FILENAME = "thumbnail.jpg"
_DESCRIPTION_FILENAME = "deskripsi.txt"
_LINK_FILENAME = "link.txt"
_UNAVAILABLE_PREFIX = "unavailable"
_FALLBACK_CHANNEL_SLUG = "channel"
_MAX_SLUG_LEN = 100
_SHORT_LIST_MSG = "Mengambil daftar video..."
_SHORT_CHANNEL_MSG = "Mengambil daftar video dari channel..."
_UNAVAILABLE_DESCRIPTION = "Video tidak tersedia (unavailable/private/unlisted)."
_NO_VIDEOS_MSG = "Channel tidak memiliki video."
_NO_FILTER_MATCH_MSG = "Tidak ada video yang cocok dengan filter."
_THUMBNAIL_TIMEOUT_SECONDS = 30
_THUMBNAIL_RETRIES = 2
_RETRY_DELAY_SECONDS = 1.5
_LIST_FAILED_TEMPLATE = "Gagal ambil daftar video: {message}"
_WATCH_URL_TEMPLATE = "https://youtu.be/{video_id}"
_SHORT_IMG_HOST = "https://i.ytimg.com/vi/{video_id}/{size}.jpg"

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")
_LEADING_INT_RE = re.compile(r"^[+-]?[0-9]+")
_DIGITS_RE = re.compile(r"\D")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ALNUM_RE = re.compile(r"[a-z0-9]")
_HTTP_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

_CHECKPOINT_LOCK = threading.Lock()


def _mark_completed(output_dir: str, video_id: str) -> None:
    """Record a completed video under a lock.

    ``checkpoint.mark_completed`` is a read-modify-write of a shared JSON file.
    Serialising it keeps concurrent workers from clobbering each other's
    entries — the legacy single-threaded event loop could not hit this.
    """
    with _CHECKPOINT_LOCK:
        mark_completed(output_dir, video_id)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def slug_or_none(text: Optional[str]) -> Optional[str]:
    """Return a slug, or ``None`` when the input is null or slugifies to nothing.

    The legacy ``slugify`` returned ``null`` in those cases; the shared
    :func:`rch.youtube.common.slugify` returns ``"video"`` instead. This wrapper
    restores the legacy tri-state so callers can tell "no usable title" apart
    from a real title that happens to slugify to ``"video"``.
    """
    if text is None:
        return None
    lowered = str(text).lower()
    if not _ALNUM_RE.search(lowered):
        return None
    return slugify(lowered)


def normalize_channel_url(channel_url: str, *, shorts: bool = False) -> str:
    """Normalise a channel id, handle, or URL into a browsable channel URL.

    Bare ids (``UC...``) and bare handles (``@name``) are expanded to canonical
    ``youtube.com`` URLs. Full URLs pass through unchanged. When ``shorts`` is
    requested the ``/shorts`` tab suffix is appended exactly once.
    """
    candidate = (channel_url or "").strip()
    if not _HTTP_SCHEME_RE.match(candidate):
        if _CHANNEL_ID_RE.match(candidate):
            candidate = f"https://www.youtube.com/channel/{candidate}"
        elif _HANDLE_RE.match(candidate):
            candidate = f"https://www.youtube.com/{candidate}"
    if shorts:
        base = candidate[:-1] if candidate.endswith("/") else candidate
        if not base.endswith("/shorts"):
            base = f"{base}/shorts"
        candidate = base
    return candidate


def channel_slug(channel_url: str) -> str:
    """Derive the output-folder stem for a channel URL.

    Mirrors the legacy ``slugify(url without scheme) || "channel"``; the
    non-alphanumeric run collapse is applied before truncation.
    """
    stripped = _HTTP_SCHEME_RE.sub("", str(channel_url or ""))
    lowered = stripped.lower()
    hyphenated = _NON_ALNUM_RE.sub("-", lowered)
    trimmed = hyphenated[:_MAX_SLUG_LEN].strip("-")
    return trimmed or _FALLBACK_CHANNEL_SLUG


def base_folder_name(video_id: str, title: Optional[str]) -> str:
    """Return the collision-prone base folder name for one video.

    Uses the slugified title; falls back to the video id when the title is
    present but unusable, and to ``unavailable-<id>`` when there is no title
    at all (private/deleted video).
    """
    slug = slug_or_none(title)
    if slug:
        return slug
    if title:
        return video_id
    return f"{_UNAVAILABLE_PREFIX}-{video_id}"


def count_slug_collisions(video_ids: List[str], meta_map: Dict[str, Dict]) -> Dict[str, int]:
    """Tally how many videos map to each base folder name."""
    counts: Dict[str, int] = {}
    for video_id in video_ids:
        info = meta_map.get(video_id) or {}
        name = base_folder_name(video_id, info.get("title"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def folder_name_for(video_id: str, title: Optional[str], slug_counts: Dict[str, int]) -> str:
    """Disambiguate a base folder name by appending the video id when shared."""
    base = base_folder_name(video_id, title)
    if (slug_counts.get(base) or 0) > 1:
        return f"{base}-{video_id}"
    return base


def parse_int(value: object) -> Optional[int]:
    """Coerce a value the way JavaScript's ``parseInt`` would.

    Returns ``None`` for null, empty, or falsy input (mirroring the legacy
    ``options.x ? parseInt(options.x, 10) : null`` guard) and for values with
    no leading integer. Trailing garbage is ignored, as in JS.
    """
    if not value:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = _LEADING_INT_RE.match(str(value).strip())
    return int(match.group(0)) if match else None


def filter_ids(video_ids: List[str], meta_map: Dict[str, Dict],
               options: Optional[Dict] = None) -> List[str]:
    """Apply min/max duration and ``after`` upload-date filters.

    Videos with unknown metadata are always kept, as are videos whose duration
    or upload date is unknown when the corresponding filter is active.
    """
    opts = options or {}
    min_duration = parse_int(opts.get("minDuration"))
    max_duration = parse_int(opts.get("maxDuration"))
    after = opts.get("after") or None
    after_compact = str(after).replace("-", "") if after else None

    kept: List[str] = []
    for video_id in video_ids:
        meta = meta_map.get(video_id)
        if not meta:
            kept.append(video_id)
            continue
        duration = meta.get("duration")
        if min_duration and duration is not None and duration < min_duration:
            continue
        if max_duration and duration is not None and duration > max_duration:
            continue
        upload_date = meta.get("uploadDate")
        if after_compact and upload_date and str(upload_date) < after_compact:
            continue
        kept.append(video_id)
    return kept


def thumbnail_url(video_id: str, size: str = _DEFAULT_SIZE) -> str:
    """Build the i.ytimg.com CDN thumbnail URL for a video id."""
    return _SHORT_IMG_HOST.format(video_id=video_id, size=size)


def build_report(result: Dict) -> Dict:
    """Shape a channel result envelope into the report dict ``write_report`` wants."""
    return {
        "channel": result.get("channel"),
        "total": result.get("total", 0),
        "success": result.get("success", 0),
        "failed": result.get("failed", 0),
        "unavailable": result.get("unavailable", 0),
        "unavailableIds": result.get("unavailableIds") or [],
        "zipPath": result.get("zipPath"),
        "items": result.get("items") or [],
    }


def watch_url(video_id: str) -> str:
    """Canonical short watch URL for a video id."""
    return _WATCH_URL_TEMPLATE.format(video_id=video_id)


# ---------------------------------------------------------------------------
# Metadata boundary (default implementation; injected in tests)
# ---------------------------------------------------------------------------


class MetadataClient:
    """Adapter over the metadata module's four lookup entry points.

    Wrapping them in one object keeps the orchestration signatures small and
    gives tests a single seam to fake. ``batch`` failures degrade to an empty
    map rather than propagating, matching the legacy try/catch.
    """

    def __init__(self, *, get_video_info=None, get_video_info_batch=None,
                 get_channel_ids=None, get_video_info_fallback=None) -> None:
        self._get_video_info = get_video_info
        self._get_video_info_batch = get_video_info_batch
        self._get_channel_ids = get_channel_ids
        self._get_video_info_fallback = get_video_info_fallback

    def list_ids(self, source_url: str) -> List[str]:
        """Enumerate video ids for a channel URL. Failures propagate."""
        if self._get_channel_ids is None:
            from .metadata import get_channel_ids

            return list(get_channel_ids(source_url))
        return list(self._get_channel_ids(source_url))

    def batch(self, ids: List[str]) -> Dict[str, Dict]:
        """Fetch metadata for many ids at once. Failures yield an empty map."""
        if self._get_video_info_batch is None:
            from .metadata import get_video_info_batch

            return dict(get_video_info_batch(ids))
        return dict(self._get_video_info_batch(ids))

    def single(self, url: str) -> Dict:
        """Fetch metadata for one video URL. Failures propagate."""
        if self._get_video_info is None:
            from .metadata import get_video_info

            return get_video_info(url)
        return self._get_video_info(url)

    def fallback(self, video_id: str) -> Optional[Dict]:
        """Lightweight oEmbed/page-title lookup. May return ``None``."""
        if self._get_video_info_fallback is None:
            from .metadata import get_video_info_fallback

            return get_video_info_fallback(video_id)
        return self._get_video_info_fallback(video_id)


def resolve_metadata(video_ids: List[str], metadata: MetadataClient,
                    on_phase: Optional[Callable[[str, str], None]] = None) -> Dict[str, Dict]:
    """Build the id -> metadata map, backfilling anything the batch missed.

    Batch results are authoritative; missing ids are retried one-by-one and
    then through the lightweight fallback. Anything still unresolved becomes a
    title-less placeholder so enumeration order is preserved.
    """
    meta_map: Dict[str, Dict] = {}
    try:
        meta_map = metadata.batch(video_ids)
    except Exception:
        meta_map = {}

    missing = [vid for vid in video_ids if not meta_map.get(vid)]
    if missing:
        _notify_phase(on_phase, "meta-fallback",
                      f"Melengkapi info {len(missing)} video yang tersisa...")

    for video_id in missing:
        try:
            meta_map[video_id] = metadata.single(watch_url(video_id))
        except Exception:
            meta_map[video_id] = metadata.fallback(video_id)
        if not meta_map.get(video_id):
            meta_map[video_id] = {
                "id": video_id,
                "title": None,
                "description": "",
                "url": watch_url(video_id),
            }
    return meta_map


def _apply_filters(video_ids: List[str], meta_map: Dict[str, Dict], options: Dict,
                   on_phase: Optional[Callable[[str, str], None]]
                   ) -> Optional[List[str]]:
    """Apply duration/date filters and report the phase. ``None`` means empty."""
    if not (options.get("minDuration") or options.get("maxDuration") or options.get("after")):
        return video_ids
    before = len(video_ids)
    kept = filter_ids(video_ids, meta_map, options)
    if len(kept) != before:
        _notify_phase(on_phase, "filter", f"Filter: {before} -> {len(kept)} video.")
    return kept


def _notify_phase(on_phase: Optional[Callable[[str, str], None]], name: str, msg: str) -> None:
    if on_phase is not None:
        on_phase(name, msg)


def _video_item(info: Dict, video_id: str, *, ok: bool, skipped: bool,
                error: Optional[str]) -> Dict:
    """Assemble one ``channel-video`` result item."""
    return {
        "id": video_id,
        "videoId": video_id,
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploadDate": info.get("uploadDate"),
        "description": info.get("description"),
        "url": watch_url(video_id),
        "ok": ok,
        "skipped": skipped,
        "error": error,
    }


def _run_workers(ids: List[str], total: int, concurrency: int,
                 worker: Callable[[str], Dict], on_done: Callable[[Dict], None],
                 on_phase: Optional[Callable[[str, str], None]],
                 on_progress: Optional[Callable[[int, int, str], None]],
                 emitter, label_of: Callable[[Dict], str]) -> List[Dict]:
    """Process ids through a bounded thread pool, preserving result order.

    Concurrency only changes throughput, not the result sequence, so callers
    get deterministic ``items`` regardless of the configured pool size.
    """
    workers = max(1, int(concurrency or 1))
    results: List[Optional[Dict]] = [None] * len(ids)
    done = 0
    lock = threading.Lock()

    def task(index: int, video_id: str) -> None:
        nonlocal done
        item = worker(video_id)
        with lock:
            results[index] = item
            done += 1
            current = done
        label = label_of(item)
        emitter.emit("progress", {"done": current, "total": total, "label": label})
        if on_progress is not None:
            on_progress(current, total, label)
        on_done(item)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda pair: task(*pair), list(enumerate(ids))))
    return [r for r in results if r is not None]


def list_ids(source_url: str, options: Optional[Dict] = None,
             metadata: Optional[MetadataClient] = None) -> List[str]:
    """Enumerate the video ids of a channel without downloading anything.

    The public counterpart to :meth:`MetadataClient.list_ids`, used by
    ``rch list`` to print a channel's ids. ``limit`` truncates the result and
    ``shorts`` switches enumeration to the channel's Shorts tab, matching what
    :func:`channel_video` does for the same options. Enumeration failures
    propagate so the caller can report them.
    """
    opts = options or {}
    client = metadata or MetadataClient()
    ids = client.list_ids(
        normalize_channel_url(source_url, shorts=bool(opts.get("shorts")))
    )
    limit = parse_int(opts.get("limit"))
    if limit and limit > 0:
        ids = ids[:limit]
    return ids


def channel_video(channel_url: str, options: Optional[Dict] = None, *,
                  list_ids: Optional[Callable[[str], List[str]]] = None,
                  metadata: Optional[MetadataClient] = None,
                  download_video: Optional[Callable[[str, Dict], Dict]] = None,
                  sleep: Optional[Callable[[float], None]] = None,
                  emitter=None) -> Dict:
    """Harvest video files for every video in a channel.

    Video-only counterpart of :func:`channel_full`: enumerates the channel,
    downloads each video into a per-video folder, zips the results, and returns
    an aggregate report. Never raises for per-video failures — those are
    recorded on the item and counted in ``failed``.

    ``list_ids`` and ``download_video`` are injectable so the mode can be
    exercised without yt-dlp; ``metadata`` supplies the four lookup primitives.
    """
    opts = options or {}
    output_dir = opts.get("outputDir") or _DEFAULT_OUTPUT_DIR
    concurrency = opts.get("concurrency") or _DEFAULT_CONCURRENCY
    quality = opts.get("quality") or _DEFAULT_QUALITY
    zip_name = opts.get("zipName") or None
    on_progress = opts.get("onProgress") if callable(opts.get("onProgress")) else None
    on_phase = opts.get("onPhase") if callable(opts.get("onPhase")) else None
    limit = parse_int(opts.get("limit"))
    resume = opts.get("resume") is True
    retry_failed = opts.get("retryFailed") is not False
    subtitles = opts.get("subtitles") is True
    sub_lang = opts.get("subLang") or _DEFAULT_SUB_LANG
    shorts = bool(opts.get("shorts"))

    meta_client = metadata or MetadataClient()
    downloader = download_video or _default_download_video
    sleeper = sleep if sleep is not None else _default_sleep
    bus = emitter if emitter is not None else create_emitter()

    source_url = normalize_channel_url(channel_url, shorts=shorts)

    bus.emit("phase", {"phase": "list", "msg": _SHORT_LIST_MSG})
    _notify_phase(on_phase, "list", _SHORT_CHANNEL_MSG)
    try:
        ids = list(list_ids(source_url)) if list_ids is not None \
            else meta_client.list_ids(source_url)
    except Exception as exc:
        return {"status": False,
                "message": _LIST_FAILED_TEMPLATE.format(message=str(exc))}

    if limit and limit > 0:
        ids = ids[:limit]
    if not ids:
        return {"status": False, "message": _NO_VIDEOS_MSG}

    _notify_phase(on_phase, "meta", f"Mengambil judul {len(ids)} video...")
    meta_map = resolve_metadata(ids, meta_client)

    filtered = _apply_filters(ids, meta_map, opts, on_phase)
    if not filtered:
        return {"status": False, "message": _NO_FILTER_MATCH_MSG}
    ids = filtered

    slug_counts = count_slug_collisions(ids, meta_map)
    slug = channel_slug(source_url)
    suffix = f"{slug}{_SHORTS_DIR_SUFFIX if shorts else ''}"
    final_zip_name = zip_name or f"{suffix}-videos.zip"
    work_dir = Path(output_dir) / f"{suffix}-videos"
    work_dir.mkdir(parents=True, exist_ok=True)

    completed_ids = _restore_checkpoint(output_dir, channel_url, len(ids), resume)
    zip_path = Path(output_dir) / final_zip_name
    zip_stream = _ZipCollector(zip_path)

    bus.emit("start", {"channel": channel_url, "total": len(ids)})
    _notify_phase(on_phase, "download", f"Mendownload {len(ids)} video...")

    def worker(video_id: str) -> Dict:
        info = meta_map.get(video_id) or {"id": video_id, "title": None}
        folder_name = folder_name_for(video_id, info.get("title"), slug_counts)
        folder_path = work_dir / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        label = info.get("title") or video_id

        bus.emit("video:start", {"id": video_id, "title": label})

        final_video_path = folder_path / _VIDEO_ARCNAME
        if resume and (final_video_path.exists() or video_id in completed_ids):
            if final_video_path.exists():
                zip_stream.add_file(str(final_video_path), f"{folder_name}/{_VIDEO_ARCNAME}")
            bus.emit("video:done", {"id": video_id, "title": label, "ok": True,
                                    "skipped": True, "error": None})
            return _video_item(info, video_id, ok=True, skipped=True, error=None)

        ok = False
        error: Optional[str] = None
        try:
            envelope = downloader(watch_url(video_id), _video_download_options(
                folder_path, quality, subtitles, sub_lang))
            if envelope.get("status"):
                zip_stream.add_file(envelope["result"]["path"],
                                    f"{folder_name}/{_VIDEO_ARCNAME}")
                ok = True
            else:
                error = envelope.get("message")
        except Exception as exc:
            error = str(exc)

        if ok:
            _mark_completed(str(output_dir), video_id)
        bus.emit("video:done", {"id": video_id, "title": label, "ok": ok,
                                "skipped": False, "error": error})
        return _video_item(info, video_id, ok=ok, skipped=False, error=error)

    results = _run_workers(ids, len(ids), concurrency, worker,
                           lambda _item: None, on_phase, on_progress, bus,
                           lambda item: item.get("title") or item.get("videoId"))

    if retry_failed:
        _retry_failed_items(results, meta_map, slug_counts, work_dir, quality,
                            subtitles, sub_lang, downloader, sleeper, zip_stream, on_phase)

    zip_stream.finalize()

    success = len([r for r in results if r["ok"]])
    failed_count = len(results) - success
    if failed_count == 0:
        clear_checkpoint(str(output_dir))

    bus.emit("complete", {"channel": channel_url, "total": len(ids),
                          "success": success, "failed": failed_count})

    return {
        "status": True,
        "result": {
            "channel": channel_url,
            "total": len(ids),
            "success": success,
            "failed": failed_count,
            "zipPath": str(zip_path),
            "workDir": str(work_dir),
            "items": results,
        },
    }


def _video_download_options(folder_path: Path, quality: str, subtitles: bool,
                            sub_lang: str) -> Dict:
    """Assemble the per-video download options for the video-only mode."""
    return {
        "format": _VIDEO_EXTENSION,
        "quality": quality,
        "outputDir": str(folder_path),
        "filename": _VIDEO_FILENAME,
        "subtitles": subtitles,
        "subLang": sub_lang,
    }


def _retry_failed_items(results: List[Dict], meta_map: Dict[str, Dict],
                        slug_counts: Dict[str, int], work_dir: Path, quality: str,
                        subtitles: bool, sub_lang: str,
                        downloader: Callable[[str, Dict], Dict],
                        sleeper: Callable[[float], None],
                        zip_stream: _ZipCollector,
                        on_phase: Optional[Callable[[str, str], None]],
                        key: str = "ok", unavailable_key: str = "skipped") -> None:
    """Second pass over failed, non-skipped items; flips them to success on recovery.

    ``key`` names the success flag on each item (``ok`` for the video-only
    mode, ``videoOk`` for the full mode) and ``unavailable_key`` names the flag
    that excludes an item from retrying.
    """
    failed = [r for r in results
              if not r.get(key) and not r.get(unavailable_key)]
    if not failed:
        return
    _notify_phase(on_phase, "retry", f"Mencoba ulang {len(failed)} video yang gagal...")
    for entry in failed:
        video_id = entry["videoId"]
        info = meta_map.get(video_id) or {"id": video_id, "title": None}
        folder_name = folder_name_for(video_id, info.get("title"), slug_counts)
        folder_path = work_dir / folder_name
        try:
            envelope = downloader(watch_url(video_id), _video_download_options(
                folder_path, quality, subtitles, sub_lang))
            if envelope.get("status"):
                zip_stream.add_file(envelope["result"]["path"],
                                    f"{folder_name}/{_VIDEO_ARCNAME}")
                entry[key] = True
                entry["error"] = None
        except Exception:
            pass
        sleeper(_RETRY_DELAY_SECONDS)


def _restore_checkpoint(output_dir: str, channel_url: str, total: int,
                        resume: bool) -> set:
    """Load previously completed ids and persist the merged checkpoint state."""
    checkpoint = load_checkpoint(str(output_dir)) if resume else None
    if not checkpoint:
        return set()
    completed = list(dict.fromkeys(checkpoint.get("completed") or []))
    if completed:
        save_checkpoint(str(output_dir), {
            "channel": channel_url,
            "total": total,
            "completed": completed,
            "failed": checkpoint.get("failed") or [],
        })
    return set(completed)


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _default_download_video(url: str, options: Dict) -> Dict:
    from .video import download

    return download(url, options)


def _default_download_image(url: str) -> bytes:
    from ..core.http import http_get

    return http_get(url, timeout=_THUMBNAIL_TIMEOUT_SECONDS,
                    retries=_THUMBNAIL_RETRIES).content


class _ZipCollector:
    """Deduplicating, thread-safe facade over a :class:`ZipStream`.

    ``zipfile`` is not safe for concurrent writes, and a resumed run can add an
    entry that the per-video pass adds again. Tracking arcnames keeps the
    archive free of duplicate entries without changing the file set.
    """

    def __init__(self, zip_path: Path) -> None:
        self._zip_path = zip_path
        self._lock = threading.Lock()
        self._stream: Optional[ZipStream] = None
        self._arcnames: set = set()

    def add_file(self, path: object, arcname: str) -> None:
        with self._lock:
            if arcname in self._arcnames:
                return
            if self._stream is None:
                self._stream = ZipStream(str(self._zip_path))
            self._stream.add_file(str(path), arcname)
            self._arcnames.add(arcname)

    def finalize(self) -> None:
        with self._lock:
            if self._stream is None:
                self._stream = ZipStream(str(self._zip_path))
            self._stream.finalize()


# ---------------------------------------------------------------------------
# channel_full / channel_info
# ---------------------------------------------------------------------------


def _full_item(info: Dict, video_id: str, *, video_ok: bool, thumb_ok: bool,
               unavailable: bool, error: Optional[str]) -> Dict:
    """Assemble one ``channel-full`` result item."""
    return {
        "id": video_id,
        "videoId": video_id,
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploadDate": info.get("uploadDate"),
        "description": info.get("description"),
        "url": watch_url(video_id),
        "videoOk": video_ok,
        "thumbOk": thumb_ok,
        "unavailable": unavailable,
        "error": error,
    }


def _write_sidecar_files(folder_path: Path, info: Dict, video_id: str,
                         unavailable: bool) -> None:
    """Write ``deskripsi.txt`` and ``link.txt`` for one video folder."""
    description = _UNAVAILABLE_DESCRIPTION if unavailable else (info.get("description") or "")
    (folder_path / _DESCRIPTION_FILENAME).write_text(description, encoding="utf-8")
    (folder_path / _LINK_FILENAME).write_text(watch_url(video_id), encoding="utf-8")


def _fetch_thumbnail(video_id: str, size: str, folder_path: Path,
                     download_image: Callable[[str], bytes]) -> bool:
    """Download the thumbnail into the folder. Returns False on any failure."""
    thumb_path = folder_path / _THUMB_FILENAME
    try:
        payload = download_image(thumbnail_url(video_id, size))
    except Exception:
        return False
    data = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")
    thumb_path.write_bytes(bytes(data))
    return True


def channel_full(channel_url: str, options: Optional[Dict] = None, *,
                 list_ids: Optional[Callable[[str], List[str]]] = None,
                 metadata: Optional[MetadataClient] = None,
                 download_video: Optional[Callable[[str, Dict], Dict]] = None,
                 download_image: Optional[Callable[[str], bytes]] = None,
                 sleep: Optional[Callable[[float], None]] = None,
                 emitter=None) -> Dict:
    """Harvest video, thumbnail, description, and link for every channel video.

    The richest of the three modes: each video folder receives ``video.mp4``,
    ``thumbnail.jpg``, ``deskripsi.txt``, and ``link.txt``, all zipped together.
    Videos whose metadata carries no title are treated as unavailable — they
    still get thumbnail/description/link entries but no video download, and are
    counted separately in the aggregate report.

    Never raises for per-video failures; those land on the item's ``error``.
    """
    opts = options or {}
    size = opts.get("size") or _DEFAULT_SIZE
    output_dir = opts.get("outputDir") or _DEFAULT_OUTPUT_DIR
    concurrency = opts.get("concurrency") or _DEFAULT_CONCURRENCY
    zip_name = opts.get("zipName") or None
    on_progress = opts.get("onProgress") if callable(opts.get("onProgress")) else None
    on_phase = opts.get("onPhase") if callable(opts.get("onPhase")) else None
    limit = parse_int(opts.get("limit"))
    include_video = opts.get("includeVideo") is not False
    quality = opts.get("quality") or _DEFAULT_QUALITY
    resume = opts.get("resume") is True
    retry_failed = opts.get("retryFailed") is not False
    subtitles = opts.get("subtitles") is True
    sub_lang = opts.get("subLang") or _DEFAULT_SUB_LANG
    shorts = bool(opts.get("shorts"))

    meta_client = metadata or MetadataClient()
    downloader = download_video or _default_download_video
    image_fetcher = download_image or _default_download_image
    sleeper = sleep if sleep is not None else _default_sleep
    bus = emitter if emitter is not None else create_emitter()

    source_url = normalize_channel_url(channel_url, shorts=shorts)

    _notify_phase(on_phase, "list", _SHORT_CHANNEL_MSG)
    bus.emit("phase", {"phase": "list", "msg": _SHORT_LIST_MSG})
    try:
        ids = list(list_ids(source_url)) if list_ids is not None \
            else meta_client.list_ids(source_url)
    except Exception as exc:
        return {"status": False,
                "message": _LIST_FAILED_TEMPLATE.format(message=str(exc))}

    if limit and limit > 0:
        ids = ids[:limit]
    if not ids:
        return {"status": False, "message": _NO_VIDEOS_MSG}

    _notify_phase(on_phase, "meta",
                  f"Mengambil info {len(ids)} video (judul + deskripsi)...")
    meta_map = resolve_metadata(ids, meta_client, on_phase)

    filtered = _apply_filters(ids, meta_map, opts, on_phase)
    if not filtered:
        return {"status": False, "message": _NO_FILTER_MATCH_MSG}
    ids = filtered

    slug = channel_slug(source_url)
    suffix = f"{slug}{_SHORTS_DIR_SUFFIX if shorts else ''}"
    final_zip_name = zip_name or f"{suffix}-full.zip"
    work_dir = Path(output_dir) / f"{suffix}-full"
    work_dir.mkdir(parents=True, exist_ok=True)

    completed_ids = _restore_checkpoint(output_dir, channel_url, len(ids), resume)
    slug_counts = count_slug_collisions(ids, meta_map)
    zip_path = Path(output_dir) / final_zip_name
    zip_stream = _ZipCollector(zip_path)

    bus.emit("start", {"channel": channel_url, "total": len(ids)})
    _notify_phase(on_phase, "download", f"Mendownload {len(ids)} video...")

    def worker(video_id: str) -> Dict:
        info = meta_map.get(video_id) or {
            "id": video_id, "title": None, "description": "",
            "url": watch_url(video_id),
        }
        label = info.get("title") or video_id
        folder_name = folder_name_for(video_id, info.get("title"), slug_counts)
        final_video_path = work_dir / folder_name / _VIDEO_ARCNAME

        bus.emit("video:start", {"id": video_id, "title": label})

        if resume and include_video and (final_video_path.exists()
                                        or video_id in completed_ids):
            if final_video_path.exists():
                zip_stream.add_file(str(final_video_path),
                                    f"{folder_name}/{_VIDEO_ARCNAME}")
            bus.emit("video:done", {"id": video_id, "title": label, "ok": True,
                                    "skipped": True, "error": None})
            return _full_item(info, video_id, video_ok=True, thumb_ok=True,
                              unavailable=False, error=None)

        unavailable = not info.get("title")
        folder_path = work_dir / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        envelope: Dict = {"status": False, "message": None}
        if include_video and not unavailable:
            envelope = downloader(watch_url(video_id), _video_download_options(
                folder_path, quality, subtitles, sub_lang))

        thumb_ok = _fetch_thumbnail(video_id, size, folder_path, image_fetcher)
        _write_sidecar_files(folder_path, info, video_id, unavailable)

        base_zip = f"{folder_name}/"
        video_ok = envelope.get("status") if include_video and not unavailable \
            else not unavailable
        if include_video and not unavailable and envelope.get("status"):
            zip_stream.add_file(envelope["result"]["path"], f"{base_zip}{_VIDEO_ARCNAME}")
        if thumb_ok:
            zip_stream.add_file(str(folder_path / _THUMB_FILENAME),
                                f"{base_zip}{_THUMB_FILENAME}")
        zip_stream.add_file(str(folder_path / _DESCRIPTION_FILENAME),
                            f"{base_zip}{_DESCRIPTION_FILENAME}")
        zip_stream.add_file(str(folder_path / _LINK_FILENAME),
                            f"{base_zip}{_LINK_FILENAME}")

        error = None if (include_video and not unavailable
                         and envelope.get("status")) else envelope.get("message")
        if video_ok:
            _mark_completed(str(output_dir), video_id)
        bus.emit("video:done", {"id": video_id, "title": label, "ok": video_ok,
                                "skipped": False, "error": error})
        return _full_item(info, video_id, video_ok=video_ok, thumb_ok=thumb_ok,
                          unavailable=unavailable, error=error)

    def guarded_worker(video_id: str) -> Dict:
        try:
            return worker(video_id)
        except Exception as exc:
            bus.emit("video:done", {"id": video_id, "title": video_id, "ok": False,
                                    "skipped": False, "error": str(exc)})
            return {"videoId": video_id, "videoOk": False, "thumbOk": False,
                    "error": str(exc)}

    results = _run_workers(ids, len(ids), concurrency, guarded_worker,
                           lambda _item: None, on_phase, on_progress, bus,
                           lambda item: item.get("title") or item.get("videoId"))

    if retry_failed and include_video:
        _retry_failed_items(results, meta_map, slug_counts, work_dir, quality,
                            subtitles, sub_lang, downloader, sleeper, zip_stream,
                            on_phase, key="videoOk", unavailable_key="unavailable")

    zip_stream.finalize()

    success = len([r for r in results if r["videoOk"]])
    failed_count = len(results) - success
    unavailable_items = [r for r in results if r.get("unavailable")]

    if failed_count == 0:
        clear_checkpoint(str(output_dir))

    bus.emit("complete", {"channel": channel_url, "total": len(ids),
                          "success": success, "failed": failed_count})

    return {
        "status": True,
        "result": {
            "channel": channel_url,
            "total": len(ids),
            "success": success,
            "failed": failed_count,
            "unavailable": len(unavailable_items),
            "unavailableIds": [r["videoId"] for r in unavailable_items],
            "zipPath": str(zip_path),
            "workDir": str(work_dir),
            "items": results,
        },
    }


def channel_info(channel_url: str, options: Optional[Dict] = None, **kwargs) -> Dict:
    """Metadata-only channel harvest: thumbnails, descriptions, and links.

    Equivalent to :func:`channel_full` with ``includeVideo=False``, matching the
    legacy ``channelInfo`` wrapper.
    """
    opts = dict(options or {})
    opts["includeVideo"] = False
    return channel_full(channel_url, opts, **kwargs)