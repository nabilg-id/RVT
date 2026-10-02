"""YouTube thumbnail URL resolution & download (Python rewrite).

Ported from ``legacy-node/lib/youtube/thumbnail.js``. The pure URL-building
helpers (``thumbnail_url``, ``THUMBNAIL_SIZES``) are covered exhaustively; the
network-bound ``thumbnail`` resolver accepts an injectable title-fetcher so it
can be unit-tested without HTTP.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from ..core.zip_util import create_zip_from_files
from .common import extract_video_id, slugify

THUMBNAIL_SIZES: List[str] = [
    "default",
    "mqdefault",
    "hqdefault",
    "sddefault",
    "maxresdefault",
]

_CDN_TEMPLATE = "https://i.ytimg.com/vi/{video_id}/{size}.jpg"


def thumbnail_url(video_id: str, size: str) -> str:
    """Build the i.ytimg.com CDN URL for a given video id and size."""
    return _CDN_TEMPLATE.format(video_id=video_id, size=size)


def _resolve_title(video_id: str, fetch_title=None) -> str:
    """Return the video title, falling back to the video id.

    When ``fetch_title`` is not supplied the oEmbed endpoint is queried, which
    is what the Node.js ``fetchTitle`` did unconditionally; a failure (offline,
    blocked, malformed response) degrades to the video id.
    """
    resolver = fetch_title
    if resolver is None:
        from .video import get_title as resolver
    try:
        return resolver(video_id) or video_id
    except Exception:
        return video_id


def thumbnail(url: str, *, sizes=None, fetch_title=None):
    """Resolve thumbnail URLs for every requested size.

    ``fetch_title`` is an optional callable ``(video_id) -> str``; when omitted
    the title is fetched from oEmbed. Returns a result envelope dict matching
    the Node.js contract: ``{"status": True, "result": {...}}`` or
    ``{"status": False, "message": ...}``.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"status": False, "message": "Invalid YouTube video URL."}

    chosen = sizes if sizes else THUMBNAIL_SIZES
    title = _resolve_title(video_id, fetch_title)

    thumbnails = {size: thumbnail_url(video_id, size) for size in chosen}
    return {
        "status": True,
        "result": {
            "id": video_id,
            "title": title,
            "thumbnails": thumbnails,
        },
    }


def download_thumbnail(url: str, *, size="maxresdefault", output_dir="./downloads",
                       filename=None, fetch_title=None, http_get=None):
    """Download a single thumbnail to ``output_dir``.

    ``http_get`` is an injectable GET callable ``(url) -> bytes`` so the network
    can be mocked. Returns a result envelope dict.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"status": False, "message": "Invalid YouTube video URL."}
    if size not in THUMBNAIL_SIZES:
        return {"status": False, "message": f"Invalid thumbnail size: {size}"}

    title = _resolve_title(video_id, fetch_title)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base = out_dir.resolve()
    file_path = (out_dir / (filename or f"{slugify(title)}-{size}.jpg")).resolve()
    if file_path.parent != base:
        return {"status": False, "message": f"Invalid filename: {filename}"}

    if http_get is None:
        from ..core.http import http_get as _default_http_get

        http_get = lambda target_url: _default_http_get(target_url).content  # noqa: E731

    try:
        data = http_get(thumbnail_url(video_id, size))
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        else:
            payload = str(data).encode("utf-8")
    except Exception as exc:
        return {"status": False, "message": str(exc)}

    with open(file_path, "wb") as f:
        f.write(payload)

    return {
        "status": True,
        "result": {
            "id": video_id,
            "title": title,
            "size": size,
            "path": str(file_path),
            "sizeBytes": len(payload),
        },
    }


def download_thumbnails(urls, *, size="maxresdefault", output_dir="./downloads",
                        concurrency=5, zip=False, zip_name=None, http_get=None,
                        fetch_title=None):
    """Download thumbnails for many video URLs (optionally into a ZIP)."""
    urls_list = urls if isinstance(urls, list) else [urls]
    results: List[Dict] = []

    def _work(u):
        return download_thumbnail(
            u,
            size=size,
            output_dir=output_dir,
            http_get=http_get,
            fetch_title=fetch_title,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_work, u): u for u in urls_list}
        for fut in as_completed(futures):
            results.append(fut.result())

    success = [r for r in results if r.get("status")]
    failed = [r for r in results if not r.get("status")]

    envelope: Dict = {
        "status": True,
        "result": {
            "total": len(urls_list),
            "downloaded": len(success),
            "failed": len(failed),
            "items": results,
        },
    }

    if zip and success:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        zip_name = zip_name or f"thumbnails-{size}.zip"
        zip_path = out_dir / zip_name
        files: List = []
        seen: set = set()
        for r in success:
            arcname = os.path.basename(r["result"]["path"])
            if arcname in seen:
                continue
            seen.add(arcname)
            files.append((r["result"]["path"], arcname))
        create_zip_from_files(files, str(zip_path))
        envelope["result"]["zipPath"] = str(zip_path)

    return envelope
