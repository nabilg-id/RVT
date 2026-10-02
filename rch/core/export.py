"""Export metadata to CSV / JSON.

Ported from the Node.js ``lib/core/export.js``.

Security: CSV cells are never written raw. Excel, LibreOffice and Google
Sheets evaluate a cell as a formula when its first character is one of
``= + - @``, so an attacker-controlled YouTube title or description such as
``=cmd|' /C calc'!A0`` becomes code execution on whoever opens the export.
Every value therefore passes through :func:`sanitize_csv_value`, which
prefixes an apostrophe so the cell is treated as literal text. Quoting alone
(what the legacy JS did) is *not* sufficient — RFC 4180 quoting does not stop
a spreadsheet from evaluating the cell.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, List, Sequence

_CSV_HEADERS: List[str] = ["id", "title", "duration", "upload_date", "url", "description"]

_CSV_ITEM_KEYS: Sequence[str] = ("id", "title", "duration", "uploadDate", "url", "description")

_FORMULA_TRIGGERS: Sequence[str] = ("=", "+", "-", "@")

_FORMULA_GUARD = "'"

_CSV_LINETERMINATOR = "\n"

_UNSUPPORTED_FORMAT = "Format tidak didukung: {fmt}"


def sanitize_csv_value(value: Any) -> str:
    """Return ``value`` as text, neutralised against spreadsheet formula injection.

    ``None`` becomes an empty string. When the value's first non-whitespace
    character is ``=``, ``+``, ``-`` or ``@``, an apostrophe is prepended so the
    cell is rendered as literal text instead of being evaluated. The apostrophe
    is placed at index 0 (not after the whitespace) because that is the only
    position a spreadsheet honours.
    """
    if value is None:
        return ""
    text = str(value)
    if text.lstrip().startswith(tuple(_FORMULA_TRIGGERS)):
        return _FORMULA_GUARD + text
    return text


def to_csv(items: Sequence[Any]) -> str:
    """Convert a sequence of metadata dicts to a CSV string.

    Uses the stdlib CSV writer so commas, quotes and newlines inside titles or
    descriptions are quoted correctly, and LF line endings so the output is
    byte-identical on every platform.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=_CSV_LINETERMINATOR)
    writer.writerow(_CSV_HEADERS)
    for item in items:
        writer.writerow([
            sanitize_csv_value(item.get(key, ""))
            for key in _CSV_ITEM_KEYS
        ])
    return buf.getvalue()


def export_metadata(items: Sequence[Any], fmt: str, file_path: str) -> str:
    """Write metadata to a CSV or JSON file and return the path.

    Raises ``ValueError`` for any format other than ``csv``/``json`` (the check
    runs before the file is created, so a rejected export leaves nothing behind).
    """
    if fmt not in ("csv", "json"):
        raise ValueError(_UNSUPPORTED_FORMAT.format(fmt=fmt))

    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "csv":
        p.write_text(to_csv(items), encoding="utf-8", newline="")
    else:
        p.write_text(
            json.dumps(list(items), indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="",
        )
    return str(p)
