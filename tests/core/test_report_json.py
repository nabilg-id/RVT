"""Tests for the JSON report and history files.

``report.txt`` was a formatted block of ``Key : value`` lines and ``history.log``
was pipe-delimited text. Both were write-only in practice: parsing the report
meant re-implementing the layout, and a channel URL containing a pipe silently
shifted every field in a history line - which the parser now has a test for.

The JSON versions are the canonical files. The legacy text files stay readable,
because people have output folders from previous runs and an audit trail that
loses its older half is worse than one that is hard to parse.

History is JSON Lines rather than one JSON array: appending a run must not mean
rewriting and risking the whole file, and a killed run must not corrupt the
entries before it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rch.core.report import (
    append_history,
    parse_history_line,
    read_history,
    read_report,
    write_report,
)

LEGACY_HISTORY = "history.log"


def _report(**overrides):
    base = {
        "channel": "https://www.youtube.com/@contoh/videos",
        "command": "channel",
        "total": 12,
        "success": 10,
        "failed": 2,
    }
    base.update(overrides)
    return base


class TestWriteReport:
    def test_the_file_is_json_and_not_text(self, tmp_path):
        path = Path(write_report(tmp_path, _report()))

        assert path.name == "report.json"
        assert not (tmp_path / "report.txt").exists()
        json.loads(path.read_text(encoding="utf-8"))

    def test_every_field_survives_the_round_trip(self, tmp_path):
        """The text version dropped anything not on its list of labels, so a new
        field silently vanished. That is the whole reason to move."""
        write_report(tmp_path, _report(unavailable=3, zipPath="out.zip",
                                       workDir="work", unavailableIds=["a", "b"]))

        data = read_report(tmp_path)
        assert data["channel"] == "https://www.youtube.com/@contoh/videos"
        assert data["command"] == "channel"
        assert data["total"] == 12
        assert data["success"] == 10
        assert data["failed"] == 2
        assert data["unavailable"] == 3
        assert data["zipPath"] == "out.zip"
        assert data["unavailableIds"] == ["a", "b"]

    def test_the_failure_list_keeps_its_detail(self, tmp_path):
        write_report(tmp_path, _report(
            fails=[{"id": "v1", "error": "403"}, {"id": "v2", "error": "timeout"}],
        ))

        fails = read_report(tmp_path)["fails"]
        assert fails == [{"id": "v1", "error": "403"},
                         {"id": "v2", "error": "timeout"}]

    def test_it_records_when_it_was_written(self, tmp_path):
        write_report(tmp_path, _report())

        assert read_report(tmp_path)["generatedAt"]

    def test_the_file_is_indented_because_a_person_opens_it(self, tmp_path):
        path = Path(write_report(tmp_path, _report()))

        assert "\n  " in path.read_text(encoding="utf-8")

    def test_a_channel_url_with_a_pipe_survives(self, tmp_path):
        """This is the bug the text format had: a pipe inside a field shifted
        every later field, so the record described a different run."""
        weird = "https://www.youtube.com/@a|b/videos"

        write_report(tmp_path, _report(channel=weird))

        assert read_report(tmp_path)["channel"] == weird

    def test_writing_twice_replaces_rather_than_appends(self, tmp_path):
        write_report(tmp_path, _report(total=1))
        write_report(tmp_path, _report(total=2))

        assert read_report(tmp_path)["total"] == 2

    def test_reading_a_report_that_was_never_written_returns_none(self, tmp_path):
        assert read_report(tmp_path) is None

    def test_a_corrupt_report_is_none_not_an_exception(self, tmp_path):
        (tmp_path / "report.json").write_text("{oops", encoding="utf-8")

        assert read_report(tmp_path) is None


class TestAppendHistory:
    def test_the_file_is_json_lines_not_a_pipe_delimited_line(self, tmp_path):
        path = Path(append_history(tmp_path, _report()))

        assert path.name == "history.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert record["total"] == 12

    def test_each_run_is_its_own_line(self, tmp_path):
        append_history(tmp_path, _report(command="one"))
        append_history(tmp_path, _report(command="two"))

        lines = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["command"] for line in lines] == ["one", "two"]

    def test_counts_are_numbers_not_strings(self, tmp_path):
        """The text format stored 'total=12'; anything summing the column had to
        strip a prefix and parse a string first."""
        append_history(tmp_path, _report())

        record = read_history(tmp_path)[0]
        assert record["total"] == 12
        assert isinstance(record["total"], int)

    def test_the_whole_video_id_list_is_kept(self, tmp_path):
        """The text line capped ids at twelve and appended '+N', which lost the
        actual ids. JSON has no line-length problem, so all of them survive."""
        ids = [f"vid{i:03d}" for i in range(40)]

        append_history(tmp_path, _report(videoIds=ids))

        assert read_history(tmp_path)[0]["videoIds"] == ids

    def test_a_channel_url_with_a_pipe_does_not_shift_fields(self, tmp_path):
        weird = "https://www.youtube.com/@a|b/videos"

        append_history(tmp_path, _report(channel=weird))

        record = read_history(tmp_path)[0]
        assert record["channel"] == weird
        assert record["command"] == "channel"
        assert record["total"] == 12

    def test_missing_counts_are_null_rather_than_the_string_dash(self, tmp_path):
        append_history(tmp_path, {"command": "bare"})

        record = read_history(tmp_path)[0]
        assert record["command"] == "bare"
        assert record["total"] is None

    def test_a_path_valued_field_does_not_lose_the_entry(self, tmp_path):
        """Callers pass zipPath and workDir as Path objects. Serialising those
        strictly would raise TypeError and drop the run from the history."""
        append_history(tmp_path, _report(zipPath=tmp_path / "out.zip"))

        assert read_history(tmp_path)[0]["zipPath"] == str(tmp_path / "out.zip")

    def test_it_records_when_it_ran(self, tmp_path):
        append_history(tmp_path, _report())

        assert read_history(tmp_path)[0]["timestamp"]


class TestLegacyHistoryIsStillReadable:
    def _write_legacy(self, tmp_path, lines):
        (tmp_path / LEGACY_HISTORY).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_an_old_folder_still_reports_its_history(self, tmp_path):
        self._write_legacy(tmp_path, [
            "2026-01-02 03:04:05 | channel | https://x/@a | total=3 | sukses=2 | gagal=1",
        ])

        records = read_history(tmp_path)
        assert len(records) == 1
        assert records[0]["channel"] == "https://x/@a"

    def test_legacy_counts_stay_strings(self, tmp_path):
        """Changing the type on the legacy path would be a silent break for
        whatever already consumes it; the json path is the new contract."""
        self._write_legacy(tmp_path, [
            "2026-01-02 03:04:05 | channel | ch | total=3 | sukses=2 | gagal=1",
        ])

        assert read_history(tmp_path)[0]["total"] == "3"

    def test_legacy_ids_are_still_surfaced(self, tmp_path):
        self._write_legacy(tmp_path, [
            "t | channel | ch | total=3 | sukses=2 | gagal=1 | ids=a1,b2",
        ])

        assert read_history(tmp_path)[0]["videoIds"] == ["a1", "b2"]

    def test_old_entries_come_before_new_ones(self, tmp_path):
        """A run that migrates must not look like the old runs never happened."""
        self._write_legacy(tmp_path, [
            "t | lama | ch | total=1 | sukses=1 | gagal=0",
        ])
        append_history(tmp_path, _report(command="baru"))

        commands = [r["command"] for r in read_history(tmp_path)]
        assert commands == ["lama", "baru"]

    def test_the_legacy_parser_still_rejects_short_lines(self, tmp_path):
        assert parse_history_line("2026-01-02 03:04:05 | only | three") is None
        assert parse_history_line("") is None

    def test_an_empty_folder_reads_as_empty(self, tmp_path):
        assert read_history(tmp_path) == []

    def test_a_corrupt_json_line_is_skipped_not_fatal(self, tmp_path):
        """One bad line must not cost the user the entire run history."""
        append_history(tmp_path, _report(command="sebelum"))
        with open(tmp_path / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        append_history(tmp_path, _report(command="sesudah"))

        commands = [r["command"] for r in read_history(tmp_path)]
        assert commands == ["sebelum", "sesudah"]


class TestReadHistoryContract:
    def test_a_bad_output_dir_type_is_still_rejected(self, tmp_path):
        with pytest.raises(TypeError):
            read_history(object())

    def test_a_directory_that_does_not_exist_is_empty(self, tmp_path):
        assert read_history(tmp_path / "never" / "created") == []

    def test_string_and_path_agree(self, tmp_path):
        append_history(tmp_path, _report())

        assert read_history(str(tmp_path)) == read_history(Path(tmp_path))
