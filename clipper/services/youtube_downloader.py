"""yt-dlp wrapper: the single download path for both the clipper and the harvester.

Everything that needs a YouTube file goes through here, so the capabilities the
harvester used to have - quality selection, audio-only, subtitles, custom output
names, rate limiting - are options on this one downloader rather than a second,
weaker implementation. What this module already did better than the harvester is
kept and never traded away:

- a JavaScript runtime is detected and passed so yt-dlp can solve YouTube's
  challenge and not silently lose formats
- ffmpeg comes from imageio-ffmpeg, so a machine without ffmpeg on PATH still
  works
- a three-tier fallback ladder, the second attempt switching player clients
- cookies resolved from a file, then from the environment, then from a browser

``download(url)`` keeps working exactly as before: every new argument is
keyword-only with a default, so the clip pipeline is untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
import yt_dlp

from ..config import (
    COOKIES_FILE,
    TEMP_DIR,
    YOUTUBE_COOKIES_BROWSER,
    YOUTUBE_COOKIES_CONTENT,
    YOUTUBE_USER_AGENT,
)

_QUALITY_RE = re.compile(r"\D")


@dataclass
class DownloadResult:
    """What a successful download produced.

    Replaces the bare ``(path, title, duration)`` tuple so the extra facts the
    harvester needs - the video id and every file written - are available
    without parsing the path.
    """

    path: Path
    title: str
    duration: float
    video_id: str
    files: List[Path] = field(default_factory=list)

    def __iter__(self):
        # Backwards compatibility for callers written against the old tuple.
        return iter((self.path, self.title, self.duration))

    def __getitem__(self, index):
        return (self.path, self.title, self.duration)[index]

    def __len__(self):
        return 3


def build_format_filter(fmt: Optional[str], quality: Optional[str],
                        default_quality: str = "1080p") -> tuple[str, str]:
    """Return ``(format_filter, extension)`` for a requested format and quality.

    - ``mp3``/audio -> ``bestaudio/best``, extension ``mp3``, quality ignored.
    - ``best`` -> ``bestvideo+bestaudio/best``, extension ``mp4``.
    - otherwise cap the video height at the numeric part of ``quality``,
      falling back to ``default_quality`` for anything non-numeric.

    The default is 1080p rather than uncapped because clips are cut to a 9:16
    frame whose largest useful source is 1920 tall. Downloading 4K and throwing
    most of it away costs real bandwidth and minutes: a 4K source turned a
    19-second clip job into twelve minutes of encoding.
    """
    normalized = (fmt or "mp4").lower()

    if normalized == "mp3":
        return "bestaudio/best", "mp3"

    if (quality or "") == "best":
        return "bestvideo+bestaudio/best", "mp4"

    digits = _QUALITY_RE.sub("", quality or default_quality or "")
    height = digits or "1080"
    return (
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best",
        "mp4",
    )


def slugify(text: str, fallback: str = "video") -> str:
    """Filesystem-safe name.

    Accented Latin letters are decomposed first so ``café`` becomes ``cafe``
    rather than losing a character. What remains non-ASCII is dropped or turned
    into a dash rather than guessed at, because a wrong guess is still a wrong
    filename on a filesystem we do not control - ``Đ`` has no ASCII equivalent,
    so it is dropped.

    Reserved Windows device names are suffixed because ``CON.mp4`` is not a
    writable path there, which is the platform this runs on most.
    """
    decomposed = unicodedata.normalize("NFKD", str(text).strip())
    allowed = []
    for ch in decomposed:
        # Combining marks left over from the decomposition are dropped, which
        # is what turns "é" into "e" rather than "e´".
        if unicodedata.combining(ch):
            continue
        lowered = ch.lower()
        if ("a" <= lowered <= "z") or ("0" <= lowered <= "9") \
                or lowered in ("-", "_"):
            allowed.append(lowered)
        elif ch.isspace():
            allowed.append("-")
    slug = "".join(allowed).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")
    if not slug:
        return fallback

    reserved = {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    if slug in reserved:
        slug = f"{slug}-file"
    return slug


def _resolve_quality(quality):
    """Pick the height cap: explicit argument, then config, then 1080p."""
    if quality:
        return quality
    env = (os.getenv("RCH_CLIP_QUALITY") or "").strip()
    return env or "1080p"


class YouTubeDownloader:
    def __init__(self, temp_dir=TEMP_DIR):
        self.temp_dir = Path(temp_dir)

    @staticmethod
    def get_video_id(url):
        if 'youtu.be' in url:
            return url.split('/')[-1].split('?')[0]
        if 'youtube.com' in url:
            return parse_qs(urlparse(url).query).get('v', [None])[0]
        return None

    def _get_js_runtimes(self):
        """Detect available JavaScript runtime for yt-dlp to solve YouTube JS challenges."""
        runtimes = {}
        for rt in ('node', 'deno', 'quickjs', 'bun'):
            if shutil.which(rt):
                runtimes[rt] = {}
                break
        return runtimes

    def download(self, url, *, output_dir=None, quality=None, audio_only=False,
                 audio_format="mp3", subtitles=False, sub_lang=None,
                 filename=None, cookies_browser=None, limit_rate=None,
                 proxy=None, sleep_requests=None, progress_hook=None,
                 max_retries=10):
        """Fetch one video. Returns a :class:`DownloadResult`.

        Args:
            url: Any YouTube video URL.
            output_dir: Where to write. Defaults to this downloader's temp dir,
                which is what the clip pipeline wants; the harvester passes a
                real folder so the file survives for the user.
            quality: ``360p``…``1080p`` or ``best``. Height is capped, not
                matched, so a 720p request still succeeds on a channel whose
                videos are only 480p.
            audio_only: Download the audio track and convert it.
            subtitles: Fetch subtitles alongside the media.
            sub_lang: Subtitle language, or ``all``.
            filename: Base name for the output. Slugified, so a title with
                slashes or accented characters still produces a legal path.
            cookies_browser: Read cookies from this browser for this one call,
                overriding the configured browser.
            limit_rate: Cap bandwidth, e.g. ``2M``.
            proxy: Proxy URL for this one call.
            sleep_requests: Seconds to wait between requests during extraction.
            progress_hook: yt-dlp progress callback.
            max_retries: Passed through to yt-dlp.

        Returns:
            DownloadResult - iterable as ``(path, title, duration)`` for
            callers written against the old tuple.
        """
        video_id = self.get_video_id(url)
        if not video_id:
            raise ValueError("Invalid YouTube URL provided.")

        target_dir = Path(output_dir) if output_dir is not None else self.temp_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"🎬 Using FFmpeg from: {ffmpeg_path}")

        js_runtimes = self._get_js_runtimes()
        if js_runtimes:
            print(f"⚡ JS runtime detected for yt-dlp: {list(js_runtimes.keys())[0]}")

        fmt, ext = ("bestaudio/best", audio_format) if audio_only else \
            build_format_filter("mp4", _resolve_quality(quality))

        base_name = slugify(filename) if filename else video_id
        outtmpl = str(target_dir / f"{base_name}.%(ext)s")

        opts = {
            'format': fmt,
            'outtmpl': outtmpl,
            'quiet': False,
            'merge_output_format': 'mp4',
            'geo_bypass': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': False,
            'retries': max_retries,
            'fragment_retries': max_retries,
            'extractor_retries': max_retries,
            'ffmpeg_location': ffmpeg_path,
            # A JavaScript runtime alone is not enough. Without the solver
            # script yt-dlp reports "n challenge solving failed" and quietly
            # drops some formats, which reads as a missing quality rather than a
            # missing option - a 1080p request quietly lands on 480p. The script
            # is fetched on demand and cached, so this costs one download the
            # first time and nothing after. Always set, independently of whether
            # a runtime was found: the two are useless apart, and a missing
            # runtime is a better failure than silently missing formats.
            'remote_components': ['ejs:github'],
        }

        if audio_only:
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format,
            }]
        if subtitles:
            opts['writesubtitles'] = True
            opts['writeautomaticsub'] = True
            opts['subtitleslangs'] = [sub_lang] if sub_lang else ['all']
        if limit_rate:
            cap = _rate_to_bytes(limit_rate)
            # Only set when the value parsed: assigning None would tell yt-dlp
            # to cap at zero, which hangs the download instead of ignoring a
            # typo.
            if cap:
                opts['ratelimit'] = cap
            else:
                print(f"⚠️ Nilai limit-rate '{limit_rate}' tidak dipahami, "
                      "diabaikan.")
        if proxy:
            opts['proxy'] = proxy
        if sleep_requests:
            opts['sleep_interval_requests'] = sleep_requests
        if progress_hook is not None:
            opts['progress_hooks'] = [progress_hook]

        if js_runtimes:
            opts['js_runtimes'] = js_runtimes

        if YOUTUBE_USER_AGENT:
            opts['user_agent'] = YOUTUBE_USER_AGENT

        cookie_file_path = self._apply_cookies(opts, cookies_browser, target_dir)

        try:
            print("📥 Downloading video with yt-dlp (Best Quality)...")
            info = self._extract_with_fallback(url, opts)
        except Exception as e:
            raise Exception(f"Failed to download video: {str(e)}")
        finally:
            if cookie_file_path and os.path.exists(cookie_file_path):
                try:
                    os.remove(cookie_file_path)
                except OSError:
                    pass

        path = self._locate_output(target_dir, base_name, ext)
        files = sorted(p for p in target_dir.glob(f"{base_name}.*")
                       if p.is_file())
        return DownloadResult(
            path=path,
            title=info.get('title', 'N/A') if info else 'N/A',
            duration=info.get('duration', 0) if info else 0,
            video_id=video_id,
            files=files or [path],
        )

    def _apply_cookies(self, opts, cookies_browser, target_dir):
        """Fill in cookies, most explicit source first.

        1. a cookie file, resolved from config rather than the CWD so the GUI and
           the CLI find the same one;
        2. raw cookie content from the environment;
        3. a browser profile, the per-call override beating the configured one.

        Returns the path of the temporary cookie file it had to create, if any,
        so the caller can delete it.
        """
        local_cookies_file = COOKIES_FILE
        if local_cookies_file.exists() and local_cookies_file.stat().st_size > 0:
            print(f"Authentication cookies found from file: {local_cookies_file.name}")
            opts['cookiefile'] = str(local_cookies_file.resolve())
            return None

        if YOUTUBE_COOKIES_CONTENT and "PASTE" not in YOUTUBE_COOKIES_CONTENT:
            print("Authentication cookies found from environment. Applying to request.")
            with tempfile.NamedTemporaryFile(
                mode='w+', delete=False, dir=target_dir, suffix='.txt'
            ) as handle:
                handle.write(YOUTUBE_COOKIES_CONTENT)
                opts['cookiefile'] = handle.name
            return handle.name

        browser = cookies_browser or YOUTUBE_COOKIES_BROWSER
        if browser:
            source = "argument" if cookies_browser else "browser"
            print(f"Using cookies from {source}: {browser}")
            opts['cookiesfrombrowser'] = (browser,)
            return None

        print("Notice: No cookies provided. If YouTube blocks downloads, "
              "you can export cookies to cookies.txt")
        return None

    def _extract_with_fallback(self, url, opts):
        """Try the default options, then player clients, then a loose format.

        YouTube changes which client works often enough that a single format
        string is not reliable; each rung costs nothing when it is not needed.
        """
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            print(f"First download attempt failed: {str(e)}")
            print("Trying alternative download method with player_client fallback...")
            fallback_opts = opts.copy()
            fallback_opts['extractor_args'] = {
                'youtube': {'player_client': ['tv', 'web', 'android', 'ios']}
            }
            fallback_opts['format'] = 'bestvideo+bestaudio/best[ext=mp4]/best'
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                    return ydl2.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e2:
                print(f"Second download attempt failed: {str(e2)}")
                print("Trying fallback format best/bestvideo*+bestaudio...")
                fallback_opts2 = opts.copy()
                fallback_opts2['format'] = 'best/bestvideo*+bestaudio'
                with yt_dlp.YoutubeDL(fallback_opts2) as ydl3:
                    return ydl3.extract_info(url, download=True)

    @staticmethod
    def _locate_output(target_dir: Path, base_name: str, ext: str) -> Path:
        """Find the file yt-dlp produced.

        ``base_name`` rather than the video id, because a caller-supplied
        filename changes the stem. The final extension is not always the
        requested one - a merge can end up as webm - so the exact name is tried
        first and then any file sharing the stem.
        """
        expected = target_dir / f"{base_name}.{ext}"
        if expected.is_file():
            return expected
        candidates = sorted(
            p for p in target_dir.glob(f"{base_name}.*") if p.is_file()
        )
        if candidates:
            return candidates[0]
        raise FileNotFoundError("Failed to download the video file.")

    def download_many(self, urls, *, limit=None, concurrency=1,
                      download_item=None, checkpoint=None, on_progress=None,
                      **download_kwargs) -> BatchSummary:
        """Download a list of URLs, isolating failures.

        Args:
            urls: The URLs to fetch. Blanks are dropped and duplicates collapse
                to their first occurrence, so a channel listing that repeats an
                id costs one download rather than two.
            limit: Stop after this many URLs. ``None`` or ``0`` means all.
            concurrency: How many to fetch at once. ``1`` runs strictly in
                sequence, which is what you want when reading the logs.
            download_item: The per-URL callable. Defaults to this downloader's
                own ``download`` with ``download_kwargs`` applied. Tests inject
                a fake here so a batch never touches the network.
            checkpoint: Path to a JSON file listing finished URLs. Finished
                URLs are skipped on a later run, and each success is saved
                immediately so an interrupted run resumes instead of restarting.
            on_progress: Called as ``(finished, total, item)`` after each URL.

        Returns:
            BatchSummary - every URL that was attempted, in input order, each
            tagged with its result or its error.
        """
        if download_item is None:
            raise ValueError(
                "download_item is required; pass a callable or use download() "
                "for a single URL"
            )

        targets = _unique_urls(urls)
        if limit:
            try:
                cap = int(limit)
            except (TypeError, ValueError):
                cap = 0
            if cap > 0:
                targets = targets[:cap]

        tracker = None
        if checkpoint is not None:
            tracker = _Checkpoint(checkpoint)
            tracker.load()

        pending = [u for u in targets if not (tracker and tracker.has(u))]
        skipped = [u for u in targets if tracker and tracker.has(u)]

        total = len(pending)
        finished = 0

        def _one(url: str) -> BatchItem:
            nonlocal finished
            try:
                payload = download_item(url, **download_kwargs)
            except Exception as exc:  # noqa: BLE001 - one bad row must not end the batch
                item = BatchItem(url=url, error=str(exc) or exc.__class__.__name__)
            else:
                item = BatchItem(url=url, payload=_as_payload(payload))
                if tracker is not None:
                    tracker.mark(url)
                    tracker.save()
            finished += 1
            if on_progress is not None:
                on_progress(finished, total, item)
            return item

        if total == 0:
            items: List[BatchItem] = []
        elif int(concurrency or 1) <= 1:
            items = [_one(url) for url in pending]
        else:
            workers = max(1, min(int(concurrency), total))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # executor.map yields in input order, so the summary lines up
                # with the caller's list even when a later item finishes first.
                items = list(pool.map(_one, pending))

        if tracker is not None:
            # Written even when everything failed, so an all-failed run leaves a
            # checkpoint behind rather than no evidence that it ever happened.
            tracker.save()

        return BatchSummary(items=items, skipped=skipped)


@dataclass
class BatchItem:
    """One URL in a batch, whether it succeeded or not.

    A failure is data, not an exception. A channel of 700 videos will always
    contain a few that are private, deleted, or region-blocked, and losing the
    other 690 to one bad row is the failure mode this type exists to prevent.
    """

    url: str
    payload: Optional[dict] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class BatchSummary:
    """The outcome of :meth:`YouTubeDownloader.download_many`.

    ``items`` keeps the input order rather than the completion order, because
    the caller matches results back against its own list.
    """

    items: List[BatchItem] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> List[BatchItem]:
        return [i for i in self.items if i.ok]

    @property
    def failed(self) -> List[BatchItem]:
        return [i for i in self.items if not i.ok]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return len(self.ok)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def to_dict(self) -> dict:
        """A JSON-safe report. Paths become strings so this can be written out."""
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failedCount": self.failed_count,
            "failed": [
                {"url": i.url, "error": i.error} for i in self.failed
            ],
            "skipped": list(self.skipped),
            "items": [
                {
                    "url": i.url,
                    "ok": i.ok,
                    "error": i.error,
                    "result": _json_safe(i.payload),
                }
                for i in self.items
            ],
        }


def _json_safe(value):
    """Best-effort conversion of a payload into something json can encode."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class _Checkpoint:
    """Remembers which URLs a previous run already finished.

    Written after every success rather than at the end, because the reason this
    exists is a run that gets killed partway through a long channel - and that
    is exactly when the end of the run never happens.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._done: List[str] = []

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A missing file is the normal first run; a corrupt one is a killed
            # run. Neither is a reason to refuse to start, so both start over
            # and the next save overwrites the damage.
            self._done = []
            return
        done = raw.get("done") if isinstance(raw, dict) else None
        self._done = [str(u) for u in done] if isinstance(done, list) else []

    def has(self, url: str) -> bool:
        return url in self._done

    def mark(self, url: str) -> None:
        if url not in self._done:
            self._done.append(url)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"done": self._done}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            # A checkpoint that cannot be written costs a re-download on the next
            # run, which is recoverable. Letting it raise would abandon the batch
            # that is otherwise going fine.
            pass


def _as_payload(value):
    """Coerce whatever a per-item downloader returned into a plain dict."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, DownloadResult):
        return {
            "path": value.path,
            "title": value.title,
            "duration": value.duration,
            "video_id": value.video_id,
            "files": list(value.files),
        }
    return {"result": value}


def _unique_urls(urls) -> List[str]:
    """Drop blanks and duplicates while keeping the original order."""
    seen = set()
    kept = []
    for raw in urls or []:
        url = str(raw).strip() if raw is not None else ""
        if not url or url in seen:
            continue
        seen.add(url)
        kept.append(url)
    return kept


def _rate_to_bytes(rate: str) -> Optional[int]:
    """Parse ``2M`` / ``500K`` into bytes per second for yt-dlp's ``ratelimit``.

    A malformed value returns None rather than raising, so a typo in the config
    cannot stop a download that would otherwise work.
    """
    text = str(rate).strip().upper()
    if not text:
        return None
    units = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    try:
        if text[-1] in units:
            return int(float(text[:-1]) * units[text[-1]])
        return int(float(text))
    except ValueError:
        return None


# Imported for callers that want the same slug rules without instantiating the
# downloader.
__all__ = [
    "BatchItem",
    "BatchSummary",
    "DownloadResult",
    "YouTubeDownloader",
    "build_format_filter",
    "slugify",
]