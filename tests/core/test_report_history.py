"""Tests for the legacy history.log parser and for read_history.

``read_history`` reads two files: the canonical ``history.jsonl`` and the legacy
``history.log``, whose records predate the JSON change. The parser below is the
reason old folders keep working, so it is still tested directly.

The reader half of the canonical format lives in ``test_report_json.py`` and the
file mechanics in ``test_report.py``; what is here is the legacy path, including
that a legacy count stays a string because changing it would silently break
whatever already consumes those records.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rch.core.report import (
    HISTORY_FILE,
    LEGACY_HISTORY_FILE,
    parse_history_line,
    read_history,
)

_VALID_LINE = (
    "2026-01-02 03:04:05 | channel-full | https://x/@ch | total=3 | sukses=2 | gagal=1"
)


def _write(tmp_path, text, name=LEGACY_HISTORY_FILE):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


class TestParseHistoryLine:
    def test_returns_none_for_too_few_fields(self):
        assert parse_history_line("2026-01-02 03:04:05 | only | three") is None

    def test_returns_none_for_empty_string(self):
        assert parse_history_line("") is None

    def test_returns_none_for_whitespace_only(self):
        assert parse_history_line("   \t  ") is None

    def test_exactly_six_fields_is_enough(self):
        record = parse_history_line("t | c | ch | total=0 | sukses=0 | gagal=0")

        assert record is not None
        assert record["total"] == "0"

    def test_parses_the_canonical_line(self):
        record = parse_history_line(_VALID_LINE)

        assert record == {
            "timestamp": "2026-01-02 03:04:05",
            "command": "channel-full",
            "channel": "https://x/@ch",
            "total": "3",
            "success": "2",
            "failed": "1",
        }

    def test_success_key_is_renamed_from_sukses_prefix(self):
        record = parse_history_line(_VALID_LINE)

        assert "sukses" not in record
        assert record["success"] == "2"

    def test_field_prefixes_are_stripped(self):
        record = parse_history_line(_VALID_LINE)

        for key in ("total", "success", "failed"):
            assert not record[key].startswith("total=")
            assert "=" not in record[key]

    def test_values_without_prefix_are_kept_as_is(self):
        record = parse_history_line("t | c | ch | 3 | 2 | 1")

        assert (record["total"], record["success"], record["failed"]) == ("3", "2", "1")

    def test_surrounding_whitespace_is_trimmed(self):
        record = parse_history_line("  t  |   c   |  ch  |  total=3  |  sukses=2  |  gagal=1  ")

        assert record["command"] == "c"
        assert record["total"] == "3"

    def test_key_order_is_fixed(self):
        record = parse_history_line(_VALID_LINE)

        assert list(record) == [
            "timestamp", "command", "channel", "total", "success", "failed",
        ]

    def test_extra_seventh_field_is_ignored(self):
        record = parse_history_line(_VALID_LINE + " | extra")

        assert len(record) == 6
        assert "extra" not in record.values()

    def test_dash_placeholder_is_preserved(self):
        record = parse_history_line("t | - | - | total=- | sukses=- | gagal=-")

        assert record["command"] == "-"
        assert record["total"] == "-"

    def test_non_string_input_is_coerced(self):
        assert parse_history_line(None) is None
        assert parse_history_line(12345) is None

    def test_pipe_inside_channel_url_does_not_break(self):
        record = parse_history_line("t | c | https://x/@a|b | total=1 | sukses=1 | gagal=0")

        assert record["channel"] == "https://x/@a"

    def test_indianese_channel_name_round_trips(self):
        record = parse_history_line(
            "2026-01-02 03:04:05 | channel | Kanal Kita | total=1 | sukses=1 | gagal=0"
        )

        assert record["channel"] == "Kanal Kita"


class TestReadHistory:
    """The legacy path. Records keep the types the text file actually held."""

    def test_returns_empty_list_when_file_missing(self, tmp_path):
        assert read_history(str(tmp_path)) == []

    def test_accepts_path_object(self, tmp_path):
        _write(tmp_path, _VALID_LINE + "\n")

        assert len(read_history(Path(tmp_path))) == 1

    def test_reads_the_json_file_when_that_is_all_there_is(self, tmp_path):
        _write(tmp_path, '{"command": "baru"}\n', name=HISTORY_FILE)

        assert read_history(tmp_path)[0]["command"] == "baru"

    def test_reads_all_valid_lines(self, tmp_path):
        _write(tmp_path, _VALID_LINE + "\n" + _VALID_LINE.replace("3", "9") + "\n")

        assert len(read_history(tmp_path)) == 2

    def test_skips_blank_lines(self, tmp_path):
        _write(tmp_path, f"\n{_VALID_LINE}\n\n\n")

        assert len(read_history(tmp_path)) == 1

    def test_skips_malformed_lines(self, tmp_path):
        _write(tmp_path, f"garbage\n{_VALID_LINE}\nonly | two\n")

        records = read_history(tmp_path)

        assert len(records) == 1
        assert records[0]["command"] == "channel-full"

    def test_empty_file_yields_empty_list(self, tmp_path):
        _write(tmp_path, "")

        assert read_history(tmp_path) == []

    def test_file_of_only_blank_lines_yields_empty_list(self, tmp_path):
        _write(tmp_path, "\n\n\n")

        assert read_history(tmp_path) == []

    def test_preserves_file_order_oldest_first(self, tmp_path):
        _write(
            tmp_path,
            "t | first | ch | total=1 | sukses=1 | gagal=0\n"
            "t | second | ch | total=2 | sukses=1 | gagal=1\n",
        )

        records = read_history(tmp_path)

        assert [r["command"] for r in records] == ["first", "second"]

    def test_every_record_has_exactly_six_keys(self, tmp_path):
        _write(tmp_path, _VALID_LINE + "\n")

        assert all(len(r) == 6 for r in read_history(tmp_path))

    @pytest.mark.parametrize("bad", [None, 1, [], {}, object()])
    def test_non_string_output_dir_raises_type_error(self, bad):
        with pytest.raises(TypeError):
            read_history(bad)

    def test_missing_nested_directory_is_not_an_error(self, tmp_path):
        assert read_history(tmp_path / "never" / "created") == []

    def test_utf8_channel_names_survive(self, tmp_path):
        _write(tmp_path, "t | channel | Kanal印尼 | total=1 | sukses=1 | gagal=0\n")

        assert read_history(tmp_path)[0]["channel"] == "Kanal印尼"

    def test_round_trips_with_append_history(self, tmp_path):
        from rch.core.report import append_history

        append_history(tmp_path, {"command": "channel", "channel": "Kanal",
                                  "total": 3, "success": 2, "failed": 1})

        record = read_history(tmp_path)[0]

        assert record["command"] == "channel"
        assert record["channel"] == "Kanal"
        # Numbers, not the strings the legacy text file held.
        assert (record["total"], record["success"], record["failed"]) == (3, 2, 1)