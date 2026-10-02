"""YouTube video/mp3 download (Python rewrite).

Ported from the Node.js ``lib/youtube/video.js``. All external boundaries
(yt-dlp subprocess, oEmbed HTTP, sleep) are injectable so the retry/format
logic can be tested without network or process spawning.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .common import extract_video_id, slugify

_RETRYABLE_MARKERS: Tuple[str, ...] = (
    "403",
    "429",
    "forbidden",
    "too many requests",
    "econnreset",
    "etimedout",
    "timed out",
    "econnrefused",
    "network",
    "unable to download",
)

_DEFAULT_FORMAT = "mp4"
_DEFAULT_QUALITY = "720p"
_DEFAULT_OUTPUT_DIR = "./downloads"
_DEFAULT_RETRIES = 2
_RETRY_BASE_DELAY_SECONDS = 2.0

_DIGITS_RE = re.compile(r"\D")

_OEMBED_URL = (
    "https://www.youtube.com/oembed"
    "?url=https://www.youtube.com/watch?v={video_id}&format=json"
)

_DOWNLOADED_BUT_MISSING = (
    "Download selesai tapi file tidak ditemukan."
)


def _track(video_id: Optional[str], event: str, **fields) -> None:
    """Record progress in the shared ledger, never letting it break a download.

    Imported lazily so a tracker write cannot become an import-time
    dependency of the pure retry/format logic, and guarded because the ledger
    is observability: a download must not fail because tracking did.
    """
    try:
        from ..core.tracker import append_event

        append_event(video_id, event, **fields)
    except Exception:  # noqa: BLE001 - tracking must never break a download
        pass
_INVALID_URL = "Invalid YouTube video URL."


# ---------------------------------------------------------------------------
# yt-dlp runner (default implementation; injectable for tests)
# ---------------------------------------------------------------------------


def default_run_ytdlp(args: List[str], *, timeout: Optional[int] = None,
                      on_line: Optional[Callable[[str], None]] = None,
                      no_sleep: bool = False, cookies: str = "") -> str:
    """Execute the real yt-dlp binary and return stdout.

    Raises ``RuntimeError`` on failure. This is the default runner used when
    the caller does not inject one; named distinctly from the ``run_ytdlp``
    parameter of :func:`download_once` so the two never collide.
    """
    import subprocess

    from ..config import build_ytdlp_args

    final_args = build_ytdlp_args(list(args), no_sleep=no_sleep, cookies=cookies)

    kwargs: Dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if timeout:
        kwargs["timeout"] = timeout

    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable, list args
            ["yt-dlp"] + final_args,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("yt-dlp tidak ditemukan. Install dengan: pip install yt-dlp") from exc

    if on_line is not None and proc.stderr:
        for line in proc.stderr.splitlines():
            stripped = line.strip()
            if stripped:
                on_line(stripped)

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


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_retryable_error(message: Optional[str]) -> bool:
    """Return True when a yt-dlp failure looks transient and worth retrying.

    Substring match against a fixed marker list, case-insensitive. This gates
    the retry loop, so it must never raise on ``None`` or empty input.
    """
    if not message:
        return False
    lowered = str(message).lower()
    return any(marker in lowered for marker in _RETRYABLE_MARKERS)


def build_format_filter(fmt: str, quality: str) -> Tuple[str, str]:
    """Return ``(format_filter, extension)`` for the requested format/quality.

    - ``mp3`` -> bestaudio/best, extension ``mp3`` (quality ignored).
    - ``best`` -> bestvideo+bestaudio/best, extension ``mp4``.
    - otherwise cap video height at the numeric part of ``quality``
      (defaulting to 720 when non-numeric), extension ``mp4``.
    """
    normalized = (fmt or _DEFAULT_FORMAT).lower()
    is_audio = normalized == "mp3"

    if is_audio:
        return "bestaudio/best", "mp3"

    if quality == "best":
        return "bestvideo+bestaudio/best", "mp4"

    digits = _DIGITS_RE.sub("", quality or "")
    height = digits or "720"
    return (
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
        "mp4",
    )


def get_title(video_id: str, *, http_get: Optional[Callable[[str], str]] = None) -> str:
    """Fetch the video title via oEmbed, falling back to the video id.

    Never raises: any network, decode, or parse failure yields ``video_id``.
    """
    if http_get is None:
        from ..core.http import http_get as _default_http_get

        def http_get(url: str) -> str:  # type: ignore[misc]
            return _default_http_get(url, timeout=5).text

    try:
        payload = http_get(_OEMBED_URL.format(video_id=video_id))
        data = json.loads(payload)
        title = data.get("title") if isinstance(data, dict) else None
        return str(title) if title else video_id
    except Exception:
        return video_id


def build_args(url: str, video_id: str, safe_title: str, options: Dict,
               output_dir: str) -> List[str]:
    """Build the yt-dlp argument list for one download.

    ``safe_title`` must already be slugified — this function only assembles
    flags so the filename-safety decision stays in one place.
    """
    fmt = (options.get("format") or _DEFAULT_FORMAT).lower()
    quality = options.get("quality") or _DEFAULT_QUALITY
    format_filter, ext = build_format_filter(fmt, quality)

    args: List[str] = ["--no-playlist", "-f", format_filter]

    if ext == "mp3":
        args += ["--extract-audio", "--audio-format", "mp3"]
    else:
        args += ["--merge-output-format", ext]

    if options.get("subtitles"):
        args += ["--write-subs", "--write-auto-subs", "--sub-lang",
                 options.get("subLang") or "all"]

    args += ["-o", str(Path(output_dir) / f"{safe_title}.%(ext)s")]
    args.append(url)
    return args


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def download_once(url: str, options: Optional[Dict] = None, *,
                  run_ytdlp: Optional[Callable[..., str]] = None,
                  fetch_title: Optional[Callable[[str], str]] = None
                  ) -> Dict:
    """Run a single download attempt.

    Raises ``ValueError`` for an invalid URL or a missing output file, and
    propagates yt-dlp failures. Returns a success envelope on completion.
    """
    opts: Dict = options if options is not None else {}

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(_INVALID_URL)

    output_dir = opts.get("outputDir") or _DEFAULT_OUTPUT_DIR
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    fmt = (opts.get("format") or _DEFAULT_FORMAT).lower()
    quality = opts.get("quality") or _DEFAULT_QUALITY
    _, ext = build_format_filter(fmt, quality)

    filename = opts.get("filename")
    if filename:
        safe_title = slugify(filename)
    else:
        resolver = fetch_title if fetch_title is not None else get_title
        try:
            title = resolver(video_id) or ""
        except Exception:
            title = ""
        safe_title = slugify(title) if title else video_id

    args = build_args(url, video_id, safe_title, opts, output_dir)
    runner = run_ytdlp if run_ytdlp is not None else default_run_ytdlp
    runner(args, no_sleep=bool(opts.get("noSleep")), cookies=opts.get("cookies", "") or "")

    final_path = Path(output_dir) / f"{safe_title}.{ext}"
    if not final_path.exists():
        _track(video_id, "download", status="failed", error=_DOWNLOADED_BUT_MISSING)
        raise ValueError(_DOWNLOADED_BUT_MISSING)

    _track(video_id, "download", status="done", path=str(final_path),
           title=safe_title)

    return {
        "status": True,
        "result": {
            "id": video_id,
            "title": safe_title,
            "format": fmt,
            "quality": quality,
            "path": str(final_path),
            "sizeBytes": final_path.stat().st_size,
        },
    }


def download(url: str, options: Optional[Dict] = None, *,
             run_ytdlp: Optional[Callable[..., str]] = None,
             fetch_title: Optional[Callable[[str], str]] = None,
             ensure_updated: Optional[Callable[[], None]] = None,
             sleep: Optional[Callable[[float], None]] = None) -> Dict:
    """Download a YouTube video with retry/backoff.

    Retryable failures (``is_retryable_error``) back off exponentially starting
    at 2s; non-retryable failures short-circuit immediately. Never raises —
    always returns an envelope dict.
    """
    opts: Dict = options if options is not None else {}
    retries = opts.get("retries", _DEFAULT_RETRIES)
    sleeper = time.sleep if sleep is None else sleep

    try:
        if ensure_updated is not None:
            ensure_updated()

        last_message = None
        for attempt in range(retries + 1):
            try:
                return download_once(
                    url,
                    opts,
                    run_ytdlp=run_ytdlp,
                    fetch_title=fetch_title,
                )
            except Exception as exc:
                last_message = str(exc)
                if not is_retryable_error(last_message) or attempt >= retries:
                    break
                sleeper(_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))

        _track(extract_video_id(url), "download", status="failed",
               error=last_message)
        return {
            "status": False,
            "message": last_message or "Download gagal.",
        }
    except Exception as exc:
        _track(extract_video_id(url), "download", status="failed", error=str(exc))
        return {"status": False, "message": str(exc)}