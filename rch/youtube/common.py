"""YouTube URL parsing and filename safety utilities.

Pure functions extracted from the legacy Node.js implementation in
the Node.js ``lib/youtube/video.js`` and the Node.js ``lib/youtube/thumbnail.js``.
These are the most critical helpers because they guard path safety (slugify
prevents path traversal) and input validation (extract_video_id guards every
downstream HTTP call).
"""
from __future__ import annotations

import re
from typing import Optional

_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com\/(?:[^/]+\/.+\/|(?:v|e(?:mbed)?|shorts|live)\/|.*[?&]v=)|"
    r"youtu\.be\/)([^\"&?/\s]{11})",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LEADING_TRAILING_HYPHEN_RE = re.compile(r"^-+|-+$")
_MAX_SLUG_LEN = 100
_FALLBACK_SLUG = "video"


def slugify(text: object) -> str:
    """Return a filesystem-safe, lowercase slug.

    - Lowercases the input.
    - Replaces every run of non ``[a-z0-9]`` characters with a single hyphen.
    - Strips leading/trailing hyphens.
    - Truncates to 100 characters.
    - Returns ``"video"`` when the result is empty (e.g. input was only symbols).

    Equivalent to the Node.js ``slugify`` in ``lib/youtube/video.js``.
    """
    lowered = str(text).lower()
    hyphenated = _NON_ALNUM_RE.sub("-", lowered)
    trimmed = _LEADING_TRAILING_HYPHEN_RE.sub("", hyphenated)
    truncated = trimmed[:_MAX_SLUG_LEN]
    if truncated.endswith("-"):
        truncated = truncated[:-1]
    return truncated or _FALLBACK_SLUG


def extract_video_id(url: str) -> Optional[str]:
    """Extract the 11-character YouTube video id from a URL.

    Recognises ``watch?v=``, ``youtu.be/``, ``shorts/``, ``embed/``, ``live/``,
    and ``e/`` forms. Returns ``None`` when the URL does not match or the id
    is not exactly 11 characters of the expected character class.

    Equivalent to the Node.js ``extractVideoId`` in ``lib/youtube/video.js``.
    """
    if not isinstance(url, str):
        return None
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None
