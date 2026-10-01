"""Report: report.txt + history.log."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

HISTORY_FILE = "history.log"
REPORT_FILE = "report.txt"

_FIELD_PREFIXES = {"total": "total=", "success": "sukses=", "failed": "gagal="}
_FIELD_ORDER = ("timestamp", "command", "channel", "total", "success", "failed")

_MIN_HISTORY_FIELDS = 6


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def parse_history_line(line: str) -> Optional[dict]:
    """Parse one ``history.log`` line into a record dict.

    Returns ``None`` when the line has fewer than the six expected
    pipe-separated fields, so malformed entries are skipped rather than
    surfacing as half-populated records.
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
    return {key: raw[key] for key in _FIELD_ORDER}


def read_history(output_dir) -> List[dict]:
    """Read and parse ``history.log`` into a list of records, newest last.

    Accepts ``str`` or ``PathLike``, matching ``write_report`` and
    ``append_history``. A missing file is not an error — it yields an empty
    list. Blank lines and malformed lines are skipped.
    """
    if not isinstance(output_dir, (str, os.PathLike)):
        raise TypeError("output_dir harus string atau PathLike")

    file_path = Path(output_dir) / HISTORY_FILE
    if not file_path.exists():
        return []

    records: List[dict] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = parse_history_line(line)
        if record is not None:
            records.append(record)
    return records


def write_report(output_dir, report):
    """Write report.txt to output_dir."""
    lines = []
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    lines.append("=== Ridikc Content Harvester — Report ===")
    lines.append(f"Waktu   : {now}")
    if report.get("channel"):
        lines.append(f"Channel : {report['channel']}")
    if report.get("command"):
        lines.append(f"Perintah: {report['command']}")
    lines.append(f"Total   : {report.get('total', '-')}")
    if "success" in report:
        lines.append(f"Sukses  : {report['success']}")
    if "failed" in report:
        lines.append(f"Gagal   : {report['failed']}")
    if report.get("unavailable"):
        lines.append(f"Unavailable: {report['unavailable']}")
    if report.get("zipPath"):
        lines.append(f"ZIP     : {report['zipPath']}")
    lines.append("")

    if report.get("fails"):
        lines.append("-- Gagal --")
        for f in report["fails"]:
            lines.append(f"  {f.get('id', '-')} -> {f.get('error', 'error')}")
        lines.append("")

    if report.get("unavailableIds"):
        lines.append("-- Unavailable --")
        for vid in report["unavailableIds"]:
            lines.append(f"  {vid}")
        lines.append("")

    content = "\n".join(lines)
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    file_path = p / REPORT_FILE
    file_path.write_text(content, encoding="utf-8", newline="\n")
    return str(file_path)


def append_history(output_dir, report):
    """Append summary line to history.log."""
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = " | ".join([
        now,
        report.get("command", "-"),
        report.get("channel", "-"),
        f"total={report.get('total', '-')}",
        f"sukses={report.get('success', '-')}",
        f"gagal={report.get('failed', '-')}",
    ])
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    file_path = p / HISTORY_FILE
    with open(file_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    return str(file_path)
