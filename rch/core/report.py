"""Report and history, as JSON.

``report.txt`` was a formatted ``Label : value`` block and ``history.log`` was
pipe-delimited text. Both were awkward to read back: the report had to be parsed
by re-implementing its layout, and a channel URL containing a pipe shifted every
later field in a history line, so the record described a different run than the
one that happened.

``report.json`` and ``history.jsonl`` are the canonical files now. History is
JSON Lines rather than a single JSON array because appending a run must not mean
rewriting the whole file - one interrupted write then costs the last entry
instead of everything before it.

The legacy text files stay readable. Existing output folders are full of them,
and an audit trail that quietly loses its older half is worse than one that is
awkward to parse, so :func:`read_history` reads both and returns the legacy
entries first, since they are the older ones.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

#: Canonical files.
REPORT_FILE = "report.json"
HISTORY_FILE = "history.jsonl"

#: Still read, never written. See the module docstring.
LEGACY_HISTORY_FILE = "history.log"
LEGACY_REPORT_FILE = "report.txt"

_FIELD_PREFIXES = {"total": "total=", "success": "sukses=", "failed": "gagal="}
_ID_FIELD_PREFIX = "ids="
_FIELD_ORDER = ("timestamp", "command", "channel", "total", "success", "failed")

_MIN_HISTORY_FIELDS = 6

#: Legacy text fields whose value is a count. Kept because the legacy reader has
#: to reproduce what those files actually contained.
_LEGACY_COUNT_KEYS = ("total", "success", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _history_record(report: dict) -> dict:
    """Shape a run into a history record with real types, not strings."""
    record = {
        "timestamp": _now(),
        "command": report.get("command"),
        "channel": report.get("channel"),
        "total": report.get("total"),
        "success": report.get("success"),
        "failed": report.get("failed"),
    }
    ids = report.get("videoIds") or []
    if ids:
        # Every id is kept. The text line capped this at twelve and appended a
        # "+N" count, which threw away the very ids the run table shows.
        record["videoIds"] = [str(v) for v in ids]
    if report.get("zipPath"):
        record["zipPath"] = report["zipPath"]
    if report.get("error"):
        record["error"] = report["error"]
    return record


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def parse_history_line(line: str) -> Optional[dict]:
    """Parse one legacy ``history.log`` line into a record dict.

    Returns ``None`` when the line has fewer than the six expected
    pipe-separated fields, so malformed entries are skipped rather than
    surfacing as half-populated records. Counts stay strings, exactly as the
    text file stored them: changing the type here would silently break whatever
    already consumes those records.
    """
    parts = [p.strip() for p in str(line).split("|")]
    if len(parts) < _MIN_HISTORY_FIELDS:
        return None
    raw = {
        "timestamp": parts[0],
        "command": parts[1],
        "channel": parts[2],
        "total": _strip_prefix(parts[3], _FIELD_PREFIXES["total"]),
        "success": _strip_prefix(parts[4], _FIELD_PREFIXES["success"]),
        "failed": _strip_prefix(parts[5], _FIELD_PREFIXES["failed"]),
    }
    record = {key: raw[key] for key in _FIELD_ORDER}

    if len(parts) > _MIN_HISTORY_FIELDS:
        extra = _strip_prefix(parts[6], _ID_FIELD_PREFIX)
        if extra != parts[6] and extra:
            record["videoIds"] = [v for v in extra.split(",") if v]
    return record


def _read_jsonl(path: Path) -> List[dict]:
    """Parse a JSON Lines file, skipping any line that will not parse.

    A truncated final line is what a killed run leaves behind, so a bad line is
    expected rather than exceptional and must not cost the entries around it.
    """
    records: List[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return records
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _read_legacy_history(path: Path) -> List[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = parse_history_line(line)
        if record is not None:
            records.append(record)
    return records


def read_history(output_dir) -> List[dict]:
    """Read the run history, newest last.

    Accepts ``str`` or ``PathLike``, matching :func:`write_report` and
    :func:`append_history`. Legacy ``history.log`` entries come first because
    they are the older ones; a folder that has both therefore reads as one
    continuous trail instead of losing its history the first time the new
    format is written.

    A missing file is not an error - it yields an empty list.
    """
    if not isinstance(output_dir, (str, os.PathLike)):
        raise TypeError("output_dir harus string atau PathLike")

    folder = Path(output_dir)
    records = _read_legacy_history(folder / LEGACY_HISTORY_FILE)
    records.extend(_read_jsonl(folder / HISTORY_FILE))
    return records


def read_report(output_dir) -> Optional[dict]:
    """Read back the last written report, or ``None`` if there is not one.

    A corrupt file is reported as absent rather than raised: the caller is a CLI
    finishing a run, and failing to print a summary is not worth aborting over.
    """
    folder = Path(output_dir)
    try:
        return json.loads((folder / REPORT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_report(output_dir, report) -> str:
    """Write ``report.json`` to output_dir and return its path.

    The whole report is stored, not the handful of fields the old text template
    listed. A field absent from that list simply did not reach the file, which is
    exactly the kind of silent data loss this format change exists to stop.
    """
    payload = dict(report or {})
    payload["generatedAt"] = _now()
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / REPORT_FILE
    file_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8", newline="\n",
    )
    return str(file_path)


def append_history(output_dir, report) -> str:
    """Append one run to ``history.jsonl`` and return its path.

    One JSON object per line, appended. Nothing is rewritten, so a crash while
    appending costs at most this run's entry and leaves every earlier one intact.
    """
    record = _history_record(report or {})
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / HISTORY_FILE
    with open(file_path, "a", encoding="utf-8", newline="\n") as fh:
        # default=str because callers pass Paths (zipPath, workDir) and losing a
        # history entry to a TypeError would be a poor trade for type fidelity.
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return str(file_path)


