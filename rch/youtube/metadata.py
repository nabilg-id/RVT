"""YouTube metadata collection (Python rewrite).

Ported from ``legacy-node/lib/youtube/metadata.js``.

Both external boundaries are injectable so the whole module is testable
without a network or a subprocess:

- ``run_ytdlp(args, *, timeout=..., no_sleep=..., cookies=...) -> str``
  wraps the yt-dlp binary.
- ``http_get(url) -> str`` returns a response body as text.

Output shapes match the Node.js contract exactly — these are plain dicts (no
``{"status": ...}`` envelope) because they are internal data carriers consumed
by the channel modules, keyed by video id, with ``camelCase`` keys for
``uploadDate`` to match ``export.py``'s expectations.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

from .common import extract_video_id

DELIM = "<<RCH_SPLIT>>"

_BATCH_FIELDS: Sequence[str] = (
    "%(id)s",
    "%(title)s",
    "%(duration)s",
    "%(upload_date)s",
    "%(description)s",
)

_DEFAULT_CHUNK_SIZE = 30
_DEFAULT_MAX_WORKERS = 3
_DEFAULT_YTDLP_TIMEOUT_MS = 90000

_THUMBNAIL_TEMPLATE = "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
_WATCH_URL_TEMPLATE = "https://youtu.be/{video_id}"

_BARE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")

_DURATION_RE = re.compile(r"[+-]?\d+")

_INVALID_JSON = "Gagal parse metadata dari yt-dlp."

_MISSING_CHANNEL_URL = "Channel URL wajib diisi."

_MISSING_YTDLP = "yt-dlp tidak ditemukan. Install dengan: pip install yt-dlp"

_OEMBED_URL_TEMPLATE = (
    "https://www.youtube.com/oembed"
    "?url=https://www.youtube.com/watch?v={video_id}&format=json"
)
_WATCH_PAGE_URL_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"

_OEMBED_TIMEOUT_SECONDS = 8
_WATCH_PAGE_TIMEOUT_SECONDS = 15

_META_TITLE_RE = re.compile(
    r"""<meta[^>]+name=["']title["'][^>]+content=["']([^"']*)["']""",
    re.IGNORECASE,
)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_YOUTUBE_SUFFIX_RE = re.compile(r"\s*-\s*YouTube\s*$", re.IGNORECASE)


def watch_url(video_id: str) -> str:
    """Return the canonical short YouTube URL for a video id."""
    return _WATCH_URL_TEMPLATE.format(video_id=video_id)


def thumbnail_url(video_id: str) -> str:
    """Return the i.ytimg.com default thumbnail URL for a video id."""
    return _THUMBNAIL_TEMPLATE.format(video_id=video_id)


def normalize_video_id(value: Any) -> Optional[str]:
    """Reduce a bare 11-character id or any YouTube URL to the bare video id.

    Returns ``None`` for anything that is not a usable id — the batch collector
    skips those rather than handing yt-dlp a nonsense target.
    """
    if not isinstance(value, str):
        return None
    from_url = extract_video_id(value)
    if from_url:
        return from_url
    candidate = value.strip()
    if _BARE_VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    return None


def _parse_duration(raw: str) -> Optional[int]:
    """Parse yt-dlp's ``%(duration)s`` output.

    Mirrors the Node.js ``parseInt(x, 10) || null``: the leading integer part
    wins (``"212.75"`` -> ``212``), anything without one becomes ``None``, and
    the literal ``0`` also becomes ``None``.
    """
    if not raw:
        return None
    match = _DURATION_RE.match(raw.strip())
    if not match:
        return None
    value = int(match.group(0))
    return value or None


def _build_record(video_id: str, title: str, duration: Optional[int],
                  upload_date: Optional[str], description: str) -> Dict[str, Any]:
    """Assemble one metadata record with derived thumbnail/url fields."""
    return {
        "id": video_id,
        "title": title or video_id,
        "duration": duration,
        "uploadDate": upload_date or None,
        "description": description or "",
        "thumbnail": thumbnail_url(video_id),
        "url": watch_url(video_id),
    }


def parse_batch_output(output: str) -> List[Dict[str, Any]]:
    """Parse the sentinel-delimited stdout of the batch ``--print`` invocation.

    Anything before the first ``<<RCH_SPLIT>>`` sentinel is discarded (yt-dlp
    warnings land there).

    Fields are read **positionally**, not after blank-line filtering: yt-dlp
    prints one line per ``--print`` field, so an empty title is a genuinely
    empty line. Filtering blanks first would slide ``duration`` into the title
    slot and mis-attribute metadata to the wrong video, so exactly one leading
    blank line (the sentinel's own line remainder) is removed and the remaining
    lines keep their positions. Everything past the fourth field is the
    description, whose internal blank lines are kept (paragraph breaks are real
    content) while trailing ones are trimmed. Records without an id are skipped.
    """
    records: List[Dict[str, Any]] = []
    for block in str(output).split(DELIM)[1:]:
        lines = block.splitlines()
        if lines and not lines[0].strip():
            lines.pop(0)
        if not lines or not lines[0].strip():
            continue

        video_id = lines[0].strip()
        title = lines[1].strip() if len(lines) > 1 else ""
        upload_date = lines[3].strip() if len(lines) > 3 else ""

        description_lines = [line.strip() for line in lines[4:]]
        while description_lines and not description_lines[-1]:
            description_lines.pop()

        records.append(_build_record(
            video_id,
            title,
            _parse_duration(lines[2]) if len(lines) > 2 else None,
            upload_date,
            "\n".join(description_lines),
        ))
    return records


def build_batch_args(urls: Sequence[str]) -> List[str]:
    """Build the yt-dlp argument list for one batch metadata chunk."""
    args: List[str] = ["--skip-download", "--no-playlist", "--no-warnings"]
    for field in (DELIM,) + tuple(_BATCH_FIELDS):
        args += ["--print", field]
    return args + list(urls)


def _chunked(items: Sequence[str], size: int) -> List[List[str]]:
    """Split ``items`` into consecutive chunks of at most ``size`` entries."""
    size = max(1, int(size))
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def get_video_info_batch(ids: Sequence[Any], *, run_ytdlp: Optional[Callable[..., str]] = None,
                         ensure_updated: Optional[Callable[[], None]] = None,
                         chunk_size: int = _DEFAULT_CHUNK_SIZE,
                         max_workers: int = _DEFAULT_MAX_WORKERS) -> Dict[str, Dict[str, Any]]:
    """Collect metadata for many videos, keyed by video id.

    ``ids`` accepts bare video ids and/or YouTube URLs; unusable entries are
    skipped. The list is chunked (30 per yt-dlp call by default) and up to
    ``max_workers`` chunks are fetched concurrently. A chunk that fails is
    dropped without affecting the others — matching the Node.js behaviour
    where a bad chunk never aborts the batch.
    """
    normalized = [vid for vid in (normalize_video_id(v) for v in ids) if vid]
    if not normalized:
        return {}

    if ensure_updated is not None:
        ensure_updated()

    runner = run_ytdlp if run_ytdlp is not None else default_run_ytdlp
    chunks = _chunked(normalized, chunk_size)

    def _parse_chunk(chunk: List[str]) -> List[Dict[str, Any]]:
        urls = [watch_url(vid) for vid in chunk]
        try:
            output = runner(build_batch_args(urls), timeout=_DEFAULT_YTDLP_TIMEOUT_MS)
        except Exception:
            return []
        return parse_batch_output(output)

    records: List[Dict[str, Any]] = []
    workers = max(1, min(max_workers, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for chunk_records in pool.map(_parse_chunk, chunks):
            records.extend(chunk_records)

    return {record["id"]: record for record in records}


def get_video_info(url_or_id: str, *, run_ytdlp: Optional[Callable[..., str]] = None,
                   ensure_updated: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
    """Fetch full metadata for one video via ``--dump-single-json``.

    Unlike :func:`get_video_info_batch` this is a single-video call, so
    yt-dlp failures and unparseable output propagate as ``RuntimeError``
    instead of being swallowed — the caller decides whether to retry or fall
    back to :func:`get_video_info_fallback`.
    """
    if ensure_updated is not None:
        ensure_updated()

    runner = run_ytdlp if run_ytdlp is not None else default_run_ytdlp
    output = runner(
        ["--dump-single-json", "--no-playlist", url_or_id],
        timeout=_DEFAULT_YTDLP_TIMEOUT_MS,
    )

    try:
        meta = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(_INVALID_JSON) from exc

    if not isinstance(meta, dict):
        raise RuntimeError(_INVALID_JSON)

    video_id = str(meta.get("id") or "")
    title = str(meta.get("title") or "")
    description = meta.get("description") or ""
    thumbnail = meta.get("thumbnail") or thumbnail_url(video_id)
    duration = meta.get("duration") or None

    return {
        "id": video_id,
        "title": title or video_id,
        "description": description,
        "thumbnail": thumbnail,
        "duration": duration,
        "url": watch_url(video_id),
    }


def get_channel_ids(channel_url: str, *, run_ytdlp: Optional[Callable[..., str]] = None,
                    ensure_updated: Optional[Callable[[], None]] = None) -> List[str]:
    """List every video id on a channel without fetching each video.

    Uses yt-dlp's ``--flat-playlist`` enumeration, which is far cheaper than
    per-video metadata calls. Returns ids in the order yt-dlp emitted them.
    """
    if not channel_url or not str(channel_url).strip():
        raise ValueError(_MISSING_CHANNEL_URL)

    if ensure_updated is not None:
        ensure_updated()

    runner = run_ytdlp if run_ytdlp is not None else default_run_ytdlp
    output = runner(["--flat-playlist", "--print", "%(id)s", channel_url])
    return [line for line in (raw.strip() for raw in str(output).splitlines()) if line]


def _default_http_get(url: str) -> str:
    """Fetch a URL as text with the browser User-Agent and legacy timeouts.

    YouTube serves a consent/bot page to default agents, so the UA matters —
    that is why the UA lives in the default implementation rather than in the
    injectable contract, which stays ``(url) -> str`` like the rest of the
    codebase.
    """
    from ..config import BROWSER_UA
    from ..core.http import http_get as _http_get

    timeout = (
        _OEMBED_TIMEOUT_SECONDS
        if "oembed" in url
        else _WATCH_PAGE_TIMEOUT_SECONDS
    )
    response = _http_get(url, timeout=timeout, headers={"User-Agent": BROWSER_UA})
    return response.text


def _extract_meta_title(html: str) -> Optional[str]:
    """Return the ``<meta name="title">`` content, if the page has one."""
    match = _META_TITLE_RE.search(html)
    return match.group(1) if match else None


def _extract_title_tag(html: str) -> Optional[str]:
    """Return the ``<title>`` text, if the page has one."""
    match = _TITLE_TAG_RE.search(html)
    return match.group(1) if match else None


def _strip_youtube_suffix(title: str) -> str:
    """Remove a trailing ``- YouTube`` and surrounding whitespace."""
    return _YOUTUBE_SUFFIX_RE.sub("", title).strip()


def _build_basic_record(video_id: str, title: str, description: str) -> Dict[str, Any]:
    """Assemble a title-only record (no duration/upload date known).

    Used by the single-video and fallback paths, which mirror the Node.js
    six-key shape — unlike the batch path, which does know the upload date.
    """
    return {
        "id": video_id,
        "title": title or video_id,
        "description": description,
        "thumbnail": thumbnail_url(video_id),
        "duration": None,
        "url": watch_url(video_id),
    }


def get_video_info_fallback(video_id: str, *,
                            http_get: Optional[Callable[[str], str]] = None
                            ) -> Optional[Dict[str, Any]]:
    """Best-effort title lookup for a video the batch call could not resolve.

    Two cheap sources are tried in the legacy order:

    1. the oEmbed API (no cookies needed),
    2. the ``<meta name="title">`` / ``<title>`` of the watch page, which often
       still renders for unlisted videos.

    Returns ``None`` when neither source yields a title, so the caller can
    report the video as unavailable.
    """
    getter = http_get if http_get is not None else _default_http_get

    try:
        payload = getter(_OEMBED_URL_TEMPLATE.format(video_id=video_id))
        data = json.loads(payload)
        title = data.get("title") if isinstance(data, dict) else None
        if title:
            return _build_basic_record(video_id, str(title), "")
    except Exception:
        pass

    try:
        html = getter(_WATCH_PAGE_URL_TEMPLATE.format(video_id=video_id))
        candidate = _extract_meta_title(html) or _extract_title_tag(html)
        title = _strip_youtube_suffix(candidate) if candidate else ""
        if title:
            return _build_basic_record(video_id, title, "")
    except Exception:
        pass

    return None


def default_run_ytdlp(args: List[str], *, timeout: Optional[int] = None,
                      no_sleep: bool = False, cookies: str = "") -> str:
    """Execute the real yt-dlp binary and return stdout.

    The default ``run_ytdlp`` used when a caller injects nothing.
    ``timeout`` is in **milliseconds** (the legacy Node.js contract) and is
    converted to seconds for :mod:`subprocess`. Raises ``RuntimeError`` on
    failure; named distinctly from the ``run_ytdlp`` parameter of the callers
    so the two never shadow each other.
    """
    import subprocess

    from ..config import build_ytdlp_args

    final_args = build_ytdlp_args(list(args), no_sleep=no_sleep, cookies=cookies)

    kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if timeout:
        kwargs["timeout"] = timeout / 1000

    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable, list args
            ["yt-dlp"] + final_args,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(_MISSING_YTDLP) from exc

    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or f"yt-dlp exited with {proc.returncode}")

    return proc.stdout or ""


def ensure_ytdlp_updated() -> None:
    """Best-effort ``yt-dlp -U`` self-update. Never raises."""
    import subprocess

    try:
        subprocess.run(  # noqa: S603 - fixed executable, no shell
            ["yt-dlp", "-U"],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        pass
