"""Tests for rch.core.report — report.txt rendering and history.log appends.

These two files are the run's audit trail, so the assertions lock down the
exact Indonesian-labelled lines, the conditional sections, and the fact that
``history.log`` appends (never truncates) across repeated runs.
"""
from __future__ import annotations

from datetime import datetime

from rch.core.report import append_history, write_report

_TIMESTAMP_LEN = len("2026-01-02 03:04:05")


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def _strip_timestamp(line):
    return line[_TIMESTAMP_LEN + 3:]


class TestWriteReport:
    def test_returns_report_path_and_creates_directory(self, tmp_path):
        out = tmp_path / "nested" / "out"

        path = write_report(out, {"total": 0})

        assert path == str(out / "report.txt")
        assert (out / "report.txt").exists()

    def test_writes_header_and_timestamp(self, tmp_path):
        write_report(tmp_path, {"total": 0})

        lines = _lines(tmp_path / "report.txt")
        assert lines[0] == "=== Ridikc Content Harvester — Report ==="
        assert lines[1].startswith("Waktu   : ")

    def test_includes_channel_command_and_total(self, tmp_path):
        write_report(tmp_path, {"channel": "Kanal Kita", "command": "channel", "total": 7})

        body = _strip_timestamp("\n".join(_lines(tmp_path / "report.txt")))
        assert "Channel : Kanal Kita" in body
        assert "Perintah: channel" in body
        assert "Total   : 7" in body

    def test_omits_channel_and_command_when_absent(self, tmp_path):
        write_report(tmp_path, {"total": 3})

        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert "Channel :" not in content
        assert "Perintah:" not in content

    def test_total_falls_back_to_dash_when_missing(self, tmp_path):
        write_report(tmp_path, {})

        assert "Total   : -" in _lines(tmp_path / "report.txt")

    def test_success_and_failed_lines_rendered_when_present(self, tmp_path):
        write_report(tmp_path, {"total": 5, "success": 4, "failed": 1})

        lines = _lines(tmp_path / "report.txt")
        assert "Sukses  : 4" in lines
        assert "Gagal   : 1" in lines

    def test_zero_counts_are_rendered(self, tmp_path):
        write_report(tmp_path, {"total": 0, "success": 0, "failed": 0})

        lines = _lines(tmp_path / "report.txt")
        assert "Sukses  : 0" in lines
        assert "Gagal   : 0" in lines

    def test_success_and_failed_absent_when_keys_missing(self, tmp_path):
        write_report(tmp_path, {"total": 5})

        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert "Sukses  :" not in content
        assert "Gagal   :" not in content

    def test_unavailable_line_only_when_truthy(self, tmp_path):
        write_report(tmp_path, {"total": 5, "unavailable": 2})
        write_report(tmp_path / "b", {"total": 5, "unavailable": 0})

        assert "Unavailable: 2" in _lines(tmp_path / "report.txt")
        assert "Unavailable:" not in (tmp_path / "b" / "report.txt").read_text(encoding="utf-8")

    def test_zip_path_line_rendered_when_present(self, tmp_path):
        write_report(tmp_path, {"total": 1, "zipPath": str(tmp_path / "out.zip")})

        assert f"ZIP     : {tmp_path / 'out.zip'}" in _lines(tmp_path / "report.txt")

    def test_zip_path_line_absent_when_missing(self, tmp_path):
        write_report(tmp_path, {"total": 1})

        assert "ZIP     :" not in (tmp_path / "report.txt").read_text(encoding="utf-8")

    def test_fails_section_lists_id_and_error(self, tmp_path):
        write_report(
            tmp_path,
            {"total": 2, "fails": [{"id": "vid1", "error": "boom"}, {"id": "vid2", "error": "nope"}]},
        )

        lines = _lines(tmp_path / "report.txt")
        assert "-- Gagal --" in lines
        assert "  vid1 -> boom" in lines
        assert "  vid2 -> nope" in lines

    def test_fail_entries_default_missing_fields(self, tmp_path):
        write_report(tmp_path, {"total": 1, "fails": [{}, {"id": "vid2"}]})

        lines = _lines(tmp_path / "report.txt")
        assert "  - -> error" in lines
        assert "  vid2 -> error" in lines

    def test_fails_section_absent_when_list_empty(self, tmp_path):
        write_report(tmp_path, {"total": 1, "fails": []})

        assert "-- Gagal --" not in (tmp_path / "report.txt").read_text(encoding="utf-8")

    def test_unavailable_ids_section_rendered(self, tmp_path):
        write_report(tmp_path, {"total": 2, "unavailableIds": ["vid1", "vid2"]})

        lines = _lines(tmp_path / "report.txt")
        assert "-- Unavailable --" in lines
        assert "  vid1" in lines
        assert "  vid2" in lines

    def test_unavailable_ids_section_absent_when_empty(self, tmp_path):
        write_report(tmp_path, {"total": 1, "unavailableIds": []})

        assert "-- Unavailable --" not in (tmp_path / "report.txt").read_text(encoding="utf-8")

    def test_content_uses_lf_line_endings(self, tmp_path):
        write_report(tmp_path, {"total": 1})

        raw = (tmp_path / "report.txt").read_bytes()
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")

    def test_overwrites_previous_report(self, tmp_path):
        write_report(tmp_path, {"total": 99})

        write_report(tmp_path, {"total": 1})

        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert "Total   : 1" in content
        assert "Total   : 99" not in content

    def test_accepts_string_output_dir(self, tmp_path):
        path = write_report(str(tmp_path), {"total": 1})

        assert path == str(tmp_path / "report.txt")


class TestAppendHistory:
    def test_returns_history_path_and_creates_directory(self, tmp_path):
        out = tmp_path / "nested" / "out"

        path = append_history(out, {"total": 0})

        assert path == str(out / "history.log")
        assert (out / "history.log").exists()

    def test_line_shape_matches_legacy_contract(self, tmp_path):
        append_history(
            tmp_path,
            {"command": "channel", "channel": "Kanal", "total": 3, "success": 2, "failed": 1},
        )

        line = _lines(tmp_path / "history.log")[0]
        assert _strip_timestamp(line) == "channel | Kanal | total=3 | sukses=2 | gagal=1"

    def test_timestamp_is_local_iso_like(self, tmp_path):
        append_history(tmp_path, {"total": 0})

        stamp = _lines(tmp_path / "history.log")[0][:_TIMESTAMP_LEN]
        assert datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")

    def test_missing_fields_render_as_dash(self, tmp_path):
        append_history(tmp_path, {})

        assert _strip_timestamp(_lines(tmp_path / "history.log")[0]) == (
            "- | - | total=- | sukses=- | gagal=-"
        )

    def test_zero_counts_render_as_zero(self, tmp_path):
        append_history(tmp_path, {"total": 0, "success": 0, "failed": 0})

        assert _strip_timestamp(_lines(tmp_path / "history.log")[0]) == (
            "- | - | total=0 | sukses=0 | gagal=0"
        )

    def test_appends_rather_than_truncates(self, tmp_path):
        append_history(tmp_path, {"command": "first"})
        append_history(tmp_path, {"command": "second"})

        lines = _lines(tmp_path / "history.log")
        assert len(lines) == 2
        assert "first" in lines[0]
        assert "second" in lines[1]

    def test_each_line_terminated_with_newline(self, tmp_path):
        append_history(tmp_path, {"command": "a"})

        raw = (tmp_path / "history.log").read_bytes()
        assert raw.endswith(b"\n")

    def test_uses_lf_line_endings(self, tmp_path):
        append_history(tmp_path, {"command": "a", "total": 1})

        raw = (tmp_path / "history.log").read_bytes()
        assert b"\r\n" not in raw

    def test_accepts_string_output_dir(self, tmp_path):
        path = append_history(str(tmp_path), {"total": 1})

        assert path == str(tmp_path / "history.log")
