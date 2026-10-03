"""Tests for rch.core.report — file mechanics of the JSON report and history.

The content contract lives in ``test_report_json.py``. What is left here is
about the files themselves: where they land, that history appends instead of
truncating, that nothing rewrites CRLF, and that a Path argument behaves the
same as a string one.

These two files are the run's audit trail, so "does it survive a second run" is
the part worth locking down.
"""
from __future__ import annotations

import json
from datetime import datetime

from rch.core.report import (
    HISTORY_FILE,
    REPORT_FILE,
    append_history,
    read_history,
    read_report,
    write_report,
)


def _records(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestReportFile:
    def test_returns_report_path_and_creates_directory(self, tmp_path):
        out = tmp_path / "nested" / "out"

        path = write_report(out, {"total": 0})

        assert path == str(out / REPORT_FILE)
        assert (out / REPORT_FILE).exists()

    def test_accepts_string_output_dir(self, tmp_path):
        path = write_report(str(tmp_path), {"total": 1})

        assert path == str(tmp_path / REPORT_FILE)

    def test_records_a_parseable_timestamp(self, tmp_path):
        write_report(tmp_path, {"total": 0})

        stamp = read_report(tmp_path)["generatedAt"]
        assert datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")

    def test_a_zero_count_is_stored_as_zero_not_dropped(self, tmp_path):
        """The text template only printed a line when the value was truthy, so
        a run that failed everything looked like it had no counts at all."""
        write_report(tmp_path, {"total": 0, "success": 0, "failed": 0})

        data = read_report(tmp_path)
        assert data["success"] == 0
        assert data["failed"] == 0

    def test_an_empty_report_is_still_valid(self, tmp_path):
        write_report(tmp_path, {})

        assert read_report(tmp_path) is not None

    def test_content_uses_lf_line_endings(self, tmp_path):
        write_report(tmp_path, {"total": 1})

        raw = (tmp_path / REPORT_FILE).read_bytes()
        assert b"\r\n" not in raw

    def test_overwrites_previous_report(self, tmp_path):
        write_report(tmp_path, {"total": 99})

        write_report(tmp_path, {"total": 1})

        assert read_report(tmp_path)["total"] == 1


class TestHistoryFile:
    def test_returns_history_path_and_creates_directory(self, tmp_path):
        out = tmp_path / "nested" / "out"

        path = append_history(out, {"total": 0})

        assert path == str(out / HISTORY_FILE)
        assert (out / HISTORY_FILE).exists()

    def test_accepts_string_output_dir(self, tmp_path):
        path = append_history(str(tmp_path), {"total": 1})

        assert path == str(tmp_path / HISTORY_FILE)

    def test_appends_rather_than_truncates(self, tmp_path):
        """The point of a JSON Lines file: a second run must not cost the first
        one its record."""
        append_history(tmp_path, {"command": "first"})
        append_history(tmp_path, {"command": "second"})

        records = _records(tmp_path / HISTORY_FILE)
        assert len(records) == 2
        assert [r["command"] for r in records] == ["first", "second"]

    def test_each_line_terminated_with_newline(self, tmp_path):
        append_history(tmp_path, {"command": "a"})

        assert (tmp_path / HISTORY_FILE).read_bytes().endswith(b"\n")

    def test_uses_lf_line_endings(self, tmp_path):
        append_history(tmp_path, {"command": "a", "total": 1})

        assert b"\r\n" not in (tmp_path / HISTORY_FILE).read_bytes()

    def test_a_missing_field_is_null_not_a_placeholder_string(self, tmp_path):
        append_history(tmp_path, {"command": "bare"})

        record = _records(tmp_path / HISTORY_FILE)[0]
        assert record["total"] is None
        assert record["channel"] is None

    def test_zero_counts_are_stored_as_zero(self, tmp_path):
        append_history(tmp_path, {"total": 0, "success": 0, "failed": 0})

        record = _records(tmp_path / HISTORY_FILE)[0]
        assert (record["total"], record["success"], record["failed"]) == (0, 0, 0)

    def test_an_appended_run_is_immediately_readable(self, tmp_path):
        append_history(tmp_path, {"command": "sekarang", "total": 4})

        assert read_history(tmp_path)[-1]["command"] == "sekarang"
