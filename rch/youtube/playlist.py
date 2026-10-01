"""YouTube playlist scraping (Python rewrite).

Ported from ``legacy-node/lib/youtube/playlist.js``. The pure helpers
(``extract_playlist_id``, ``parse_playlist_data``) are unit-tested directly;
``scrape`` accepts an injectable ``fetch_html`` callable so it can be tested
without network access.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

_PLAYLIST_ID_RE = re.compile(r"[?&]list=([^\"&?/\s]+)", re.IGNORECASE)
_INITIAL_DATA_RE = re.compile(
    r"var\s+ytInitialData\s*=\s*(\{.*?\});",
    re.DOTALL,
)
_FALLBACK_TITLE = "Untitled Playlist"
_FALLBACK_AUTHOR = "Unknown"
_FALLBACK_VIDEO_TITLE = "Untitled Video"

_WATCH_URL_TEMPLATE = "https://youtu.be/{video_id}"

#: Column keys ``rch.core.export.to_csv`` reads. Playlist items are normalised
#: onto exactly this set so a playlist exports through the same writer as a
#: channel — a missing key would silently become a blank cell.
_EXPORT_KEYS = ("id", "title", "duration", "uploadDate", "url", "description")


def extract_playlist_id(url: str) -> Optional[str]:
    """Extract the ``list=`` parameter from a YouTube playlist URL."""
    if not isinstance(url, str):
        return None
    match = _PLAYLIST_ID_RE.search(url)
    return match.group(1) if match else None


def _extract_text(node) -> Optional[str]:
    """Pull a human-readable string from a YouTube text node."""
    if not isinstance(node, dict):
        return None
    if "simpleText" in node and node["simpleText"]:
        return str(node["simpleText"])
    runs = node.get("runs")
    if runs and isinstance(runs, list):
        joined = "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
        if joined:
            return joined
    return None


def _extract_thumbnail_url(node) -> Optional[str]:
    """Pull the first thumbnail URL from a thumbnail node, recursively."""
    if isinstance(node, dict):
        thumbs = node.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            first = thumbs[0]
            if isinstance(first, dict) and first.get("url"):
                return first["url"]
        for value in node.values():
            found = _extract_thumbnail_url(value)
            if found:
                return found
    elif isinstance(node, list):
        for entry in node:
            found = _extract_thumbnail_url(entry)
            if found:
                return found
    return None


def _collect_videos(data, items):
    """Recursively traverse ytInitialData collecting playlistVideoRenderer entries."""
    if isinstance(data, dict):
        if "playlistVideoRenderer" in data and isinstance(data["playlistVideoRenderer"], dict):
            renderer = data["playlistVideoRenderer"]
            vid = renderer.get("videoId")
            if vid:
                title = _extract_text(renderer.get("title"))
                length_text = _extract_text(renderer.get("lengthText"))
                thumb = _extract_thumbnail_url(renderer.get("thumbnail"))
                items.append({
                    "videoId": vid,
                    "title": title if title else _FALLBACK_VIDEO_TITLE,
                    "lengthText": length_text,
                    "thumbnail": thumb,
                })
        for value in data.values():
            _collect_videos(value, items)
    elif isinstance(data, list):
        for entry in data:
            _collect_videos(entry, items)


def parse_playlist_data(data: dict) -> Dict:
    """Traverse a parsed ``ytInitialData`` dict and return playlist metadata.

    Returns ``{"id", "title", "author", "thumbnail", "items"}`` where
    ``items`` is a list of ``{"videoId", "title", "lengthText", "thumbnail"}``.
    """
    if not isinstance(data, dict):
        return {
            "title": _FALLBACK_TITLE,
            "author": _FALLBACK_AUTHOR,
            "thumbnail": None,
            "items": [],
        }

    header = data.get("header", {})
    renderer = header.get("playlistHeaderRenderer", {}) if isinstance(header, dict) else {}

    title = _extract_text(renderer.get("title")) or _FALLBACK_TITLE

    author = _extract_text(renderer.get("ownerText"))
    if not author:
        owner_ep = renderer.get("ownerEndpoint")
        if isinstance(owner_ep, dict):
            browse = owner_ep.get("browseEndpoint", {})
            canonical = browse.get("canonicalBaseUrl") if isinstance(browse, dict) else None
            author = canonical or _FALLBACK_AUTHOR
        else:
            author = _FALLBACK_AUTHOR

    thumb_url = None
    banner = renderer.get("playlistHeaderBanner")
    if isinstance(banner, dict):
        thumb_url = _extract_thumbnail_url(banner)
    if thumb_url is None:
        thumbs_root = renderer.get("thumbnails")
        thumb_url = _extract_thumbnail_url(thumbs_root)

    items: List[Dict] = []
    _collect_videos(data, items)

    return {
        "title": title,
        "author": author,
        "thumbnail": thumb_url,
        "items": items,
    }


def _canonical_playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def scrape(url: str, *, fetch_html: Optional[Callable[[str], str]] = None) -> Dict:
    """Scrape a YouTube playlist page.

    ``fetch_html`` is an injectable ``(playlist_url) -> html_str`` callable so
    the network can be mocked. Returns a result envelope dict matching the
    Node.js contract.
    """
    try:
        playlist_id = extract_playlist_id(url)
        if not playlist_id:
            raise ValueError("Must be a YouTube playlist URL (with ?list=...).")

        canonical_url = _canonical_playlist_url(playlist_id)

        if fetch_html is None:
            from ..core.http import http_get

            response = http_get(canonical_url)
            html = response.text
        else:
            html = fetch_html(canonical_url)

        match = _INITIAL_DATA_RE.search(html)
        if not match:
            raise ValueError("Could not find ytInitialData in the playlist page.")

        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse ytInitialData JSON: {exc}")

        parsed = parse_playlist_data(data)

        return {
            "status": True,
            "result": {
                "id": playlist_id,
                "url": canonical_url,
                "type": "playlist",
                "title": parsed["title"],
                "author": parsed["author"],
                "thumbnail": parsed["thumbnail"],
                "itemCount": len(parsed["items"]),
                "items": parsed["items"],
            },
        }
    except Exception as exc:
        return {
            "status": False,
            "message": str(exc) or "Failed to parse YouTube playlist.",
        }


# ---------------------------------------------------------------------------
# Item normalisation
#
# ``scrape`` returns raw ``ytInitialData`` items whose length is a human
# "3:21" string. The CLI, the web GUI and the CSV/JSON exporter all need a
# machine-readable duration and the fixed export column set, so the translation
# lives here as pure functions rather than being repeated at each call site.
# ---------------------------------------------------------------------------


def parse_length_text(text: Any) -> Optional[int]:
    """Convert a YouTube ``lengthText`` into whole seconds.

    Accepts ``"M:SS"`` and ``"H:MM:SS"``, tolerating surrounding whitespace.
    Returns ``None`` for anything else — live streams carry ``"LIVE"`` and
    unlisted entries carry no length at all, and neither should become a
    fabricated duration.

    Parsing is strict on purpose: ``"3:"`` and ``":30"`` each contain one real
    number, and a permissive scan would report them as 3 s and 30 s. A wrong
    duration reads as a correct one, which is worse than no duration, so every
    component must be present and numeric.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None
    parts = candidate.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) < 3:
        numbers.insert(0, 0)
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def to_metadata(items: Optional[Sequence[Any]]) -> Dict[str, Dict[str, Any]]:
    """Normalise raw playlist items into export-shaped records, keyed by video id.

    Entries without a usable ``videoId`` are dropped — they cannot be linked,
    downloaded, or de-duplicated. A repeated id keeps its first record so the
    playlist order is preserved and the caller's list is never mutated.
    """
    meta: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("videoId") or "")
        if not video_id or video_id in meta:
            continue
        record = {key: None for key in _EXPORT_KEYS}
        record.update({
            "id": video_id,
            "title": item.get("title") or video_id,
            "duration": parse_length_text(item.get("lengthText")),
            "url": _WATCH_URL_TEMPLATE.format(video_id=video_id),
            "description": "",
            "thumbnail": item.get("thumbnail"),
        })
        meta[video_id] = record
    return meta


def watch_urls(items: Optional[Sequence[Any]]) -> List[str]:
    """Return the canonical short URL of every item that carries a video id."""
    urls: List[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("videoId") or "")
        if video_id:
            urls.append(_WATCH_URL_TEMPLATE.format(video_id=video_id))
    return urls


def playlist_metadata(url: str, options: Optional[Dict] = None,
                      scrape: Optional[Callable[..., Dict]] = None) -> Dict:
    """Scrape a playlist and return its payload with ``limit`` already applied.

    ``scrape`` is injectable so callers (and the test suite) can bypass the
    network. A failure envelope and a non-dict ``result`` pass through untouched
    — only a successful, dict-shaped result is trimmed — and the trimmed copy
    never mutates the envelope the scraper returned.
    """
    opts = options or {}
    # Resolved through the module namespace, not bound at def time, so a test
    # that patches ``playlist.scrape`` is actually honoured.
    fetcher = scrape if scrape is not None else globals()["scrape"]
    envelope = fetcher(url)

    if not envelope.get("status") or not isinstance(envelope.get("result"), dict):
        return envelope

    limit = parse_limit(opts.get("limit"))
    if not limit:
        return envelope

    items = list(envelope["result"].get("items") or [])[:limit]
    trimmed = dict(envelope)
    trimmed["result"] = {**envelope["result"], "items": items, "itemCount": len(items)}
    return trimmed


def parse_limit(value: Any) -> Optional[int]:
    """Coerce an option value to a positive integer limit, or ``None``.

    Mirrors ``rch.youtube.channel.parse_int`` for the option shapes a caller can
    realistically pass (CLI ints, JSON numbers/strings, blanks): anything that
    is not a positive number means "no limit" rather than "download nothing",
    because ``limit=0`` almost always means "flag not set" from a JSON body.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None
