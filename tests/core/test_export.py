"""Tests for rch.core.export — metadata CSV/JSON serialisation.

Security-critical section: :class:`TestCsvFormulaInjection`. A CSV cell that
begins with ``=``, ``+``, ``-`` or ``@`` is executed as a formula by Excel,
LibreOffice and Google Sheets, so any YouTube title or description carrying
one of those leading characters becomes remote code execution on whoever opens
the export. Every column is asserted, because title/description are
attacker-influenced text while id/url are not.

Values are verified by parsing the CSV back with :mod:`csv`, so the assertions
describe what a spreadsheet actually receives.
"""
from __future__ import annotations

import csv
import io
import json

import pytest

from rch.core.export import export_metadata, to_csv

_HEADERS = ["id", "title", "duration", "upload_date", "url", "description"]

_FORMULA_PAYLOADS = [
    "=cmd|' /C calc'!A0",
    "+cmd|' /C calc'!A0",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(1+1)*cmd|' /C calc'!A0",
    "=1+1",
    '=HYPERLINK("http://evil.example","click")',
]


def _rows(csv_text):
    return list(csv.reader(io.StringIO(csv_text)))


class TestToCsvStructure:
    def test_emits_exact_header_row(self):
        rows = _rows(to_csv([]))

        assert rows[0] == _HEADERS

    def test_header_only_for_empty_item_list(self):
        assert _rows(to_csv([])) == [_HEADERS]

    def test_one_row_per_item(self):
        items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

        rows = _rows(to_csv(items))

        assert len(rows) == 4

    def test_maps_camel_case_upload_date_to_snake_case_header(self):
        rows = _rows(to_csv([{"id": "vid1", "uploadDate": "20260101"}]))

        assert rows[1][_HEADERS.index("upload_date")] == "20260101"

    def test_populates_all_columns_in_header_order(self):
        items = [{
            "id": "vid00000001",
            "title": "Judul",
            "duration": 212,
            "uploadDate": "20260101",
            "url": "https://youtu.be/vid00000001",
            "description": "Deskripsi",
        }]

        rows = _rows(to_csv(items))

        assert rows[1] == [
            "vid00000001",
            "Judul",
            "212",
            "20260101",
            "https://youtu.be/vid00000001",
            "Deskripsi",
        ]

    def test_missing_keys_render_as_empty_strings(self):
        rows = _rows(to_csv([{"id": "vid00000001"}]))

        assert rows[1] == ["vid00000001", "", "", "", "", ""]

    def test_empty_item_list_yields_header_line_only(self):
        assert to_csv([]) == "id,title,duration,upload_date,url,description\n"

    def test_null_values_render_as_empty_strings(self):
        rows = _rows(to_csv([{"id": None, "title": None, "description": None}]))

        assert rows[1] == ["", "", "", "", "", ""]

    def test_non_ascii_survives_round_trip(self):
        rows = _rows(to_csv([{"id": "vid1", "title": "Halo Dunia — Änderung"}]))

        assert rows[1][1] == "Halo Dunia — Änderung"

    def test_embedded_comma_is_quoted_not_split(self):
        rows = _rows(to_csv([{"id": "vid1", "title": "One, Two, Three"}]))

        assert rows[1][1] == "One, Two, Three"
        assert len(rows[1]) == len(_HEADERS)

    def test_embedded_double_quote_is_escaped(self):
        rows = _rows(to_csv([{"id": "vid1", "description": 'He said "hi"'}]))
        assert rows[1][5] == 'He said "hi"'

    def test_embedded_newline_is_quoted_and_preserved(self):
        rows = _rows(to_csv([{"id": "vid1", "description": "line1\nline2"}]))

        assert rows[1][5] == "line1\nline2"
        assert len(rows) == 2

    def test_accepts_tuple_of_items(self):
        rows = _rows(to_csv(({"id": "vid1"},)))

        assert rows[1][0] == "vid1"

    def test_does_not_mutate_input_items(self):
        items = [{"id": "vid1", "title": "Judul"}]
        snapshot = [dict(item) for item in items]

        to_csv(items)

        assert items == snapshot

    def test_extra_keys_are_ignored(self):
        rows = _rows(to_csv([{"id": "vid1", "thumbnail": "https://img"}]))

        assert rows[1] == ["vid1", "", "", "", "", ""]


class TestToCsvLineEndings:
    def test_uses_lf_line_endings(self):
        assert "\r\n" not in to_csv([{"id": "vid1"}])

    def test_line_count_matches_row_count(self):
        rows = _rows(to_csv([{"id": "a"}, {"id": "b"}]))

        assert len(rows) == 3


class TestCsvFormulaInjection:
    @pytest.mark.parametrize("payload", _FORMULA_PAYLOADS)
    def test_title_payload_is_prefixed_with_apostrophe(self, payload):
        rows = _rows(to_csv([{"id": "vid1", "title": payload}]))

        assert rows[1][1] == f"'{payload}"

    @pytest.mark.parametrize("payload", _FORMULA_PAYLOADS)
    def test_description_payload_is_prefixed_with_apostrophe(self, payload):
        rows = _rows(to_csv([{"id": "vid1", "description": payload}]))

        assert rows[1][5] == f"'{payload}"

    @pytest.mark.parametrize("column", _HEADERS)
    @pytest.mark.parametrize("payload", ["=1+1", "+cmd", "-cmd", "@cmd"])
    def test_every_column_is_sanitised(self, column, payload):
        items = [{"id": "vid1", "title": "Judul", "duration": 1, "uploadDate": "20260101",
                  "url": "https://youtu.be/vid1", "description": "Deskripsi"}]
        source_key = "uploadDate" if column == "upload_date" else column
        items[0][source_key] = payload

        rows = _rows(to_csv(items))

        assert rows[1][_HEADERS.index(column)] == f"'{payload}"

    def test_dangerous_characters_elsewhere_are_not_touched(self):
        rows = _rows(to_csv([{"id": "vid1", "title": "1+1=2 math is fine"}]))

        assert rows[1][1] == "1+1=2 math is fine"

    def test_ordinary_leading_characters_are_not_touched(self):
        rows = _rows(to_csv([{"id": "vid1", "title": "(live) Reaction #2"}]))

        assert rows[1][1] == "(live) Reaction #2"

    def test_url_with_scheme_is_not_sanitised(self):
        url = "https://youtu.be/vid1"

        rows = _rows(to_csv([{"id": "vid1", "url": url}]))

        assert rows[1][4] == url

    def test_ordinary_numeric_duration_is_not_sanitised(self):
        rows = _rows(to_csv([{"id": "vid1", "duration": 212}]))

        assert rows[1][2] == "212"

    def test_leading_whitespace_then_formula_is_still_sanitised(self):
        rows = _rows(to_csv([{"id": "vid1", "title": " =1+1"}]))

        assert rows[1][1].lstrip().startswith("'")

    def test_sanitised_cell_stays_a_single_column(self):
        payload = "=cmd|' /C calc'!A0"

        rows = _rows(to_csv([{"id": "vid1", "description": payload}]))

        assert len(rows[1]) == len(_HEADERS)
        assert rows[1][5] == f"'{payload}"

    def test_injection_through_title_and_description_together(self):
        items = [{"id": "vid1", "title": "=1+1", "description": "@SUM(A1:A2)"}]

        rows = _rows(to_csv(items))

        assert rows[1] == ["vid1", "'=1+1", "", "", "", "'@SUM(A1:A2)"]


class TestExportMetadataCsv:
    def test_writes_csv_file_and_returns_path(self, tmp_path):
        out = tmp_path / "meta.csv"

        path = export_metadata([{"id": "vid1", "title": "Judul"}], "csv", str(out))

        assert path == str(out)
        assert out.exists()

    def test_file_content_matches_to_csv(self, tmp_path):
        out = tmp_path / "meta.csv"
        items = [{"id": "vid1", "title": "Judul", "duration": 10}]

        export_metadata(items, "csv", str(out))

        assert out.read_text(encoding="utf-8", newline="") == to_csv(items)

    def test_written_csv_is_injection_safe(self, tmp_path):
        out = tmp_path / "meta.csv"

        export_metadata([{"id": "vid1", "title": "=cmd|calc"}], "csv", str(out))

        rows = _rows(out.read_text(encoding="utf-8", newline=""))
        assert rows[1][1] == "'=cmd|calc"

    def test_written_csv_uses_lf_line_endings(self, tmp_path):
        out = tmp_path / "meta.csv"

        export_metadata([{"id": "vid1"}, {"id": "vid2"}], "csv", str(out))

        assert "\r\n" not in out.read_text(encoding="utf-8", newline="")

    def test_creates_missing_parent_directories(self, tmp_path):
        out = tmp_path / "deeply" / "nested" / "meta.csv"

        export_metadata([{"id": "vid1"}], "csv", str(out))

        assert out.exists()

    def test_accepts_path_objects(self, tmp_path):
        out = tmp_path / "meta.csv"

        path = export_metadata([{"id": "vid1"}], "csv", out)

        assert path == str(out)


class TestExportMetadataJson:
    def test_writes_json_file_and_returns_path(self, tmp_path):
        out = tmp_path / "meta.json"

        path = export_metadata([{"id": "vid1"}], "json", str(out))

        assert path == str(out)
        assert out.exists()

    def test_content_is_indented_json(self, tmp_path):
        out = tmp_path / "meta.json"

        export_metadata([{"id": "vid1", "title": "Judul"}], "json", str(out))

        raw = out.read_text(encoding="utf-8")
        assert json.loads(raw) == [{"id": "vid1", "title": "Judul"}]
        assert "\n  " in raw

    def test_non_ascii_is_not_escaped(self, tmp_path):
        out = tmp_path / "meta.json"

        export_metadata([{"id": "vid1", "title": "Halo Änderung"}], "json", str(out))

        assert "Halo Änderung" in out.read_text(encoding="utf-8")

    def test_empty_list_writes_empty_array(self, tmp_path):
        out = tmp_path / "meta.json"

        export_metadata([], "json", str(out))

        assert json.loads(out.read_text(encoding="utf-8")) == []

    def test_json_values_are_not_sanitised(self, tmp_path):
        out = tmp_path / "meta.json"

        export_metadata([{"id": "vid1", "title": "=1+1"}], "json", str(out))

        assert json.loads(out.read_text(encoding="utf-8"))[0]["title"] == "=1+1"

    def test_written_json_uses_lf_line_endings(self, tmp_path):
        out = tmp_path / "meta.json"

        export_metadata([{"id": "vid1"}, {"id": "vid2"}], "json", str(out))

        assert "\r\n" not in out.read_text(encoding="utf-8", newline="")

    def test_creates_missing_parent_directories(self, tmp_path):
        out = tmp_path / "deeply" / "nested" / "meta.json"

        export_metadata([{"id": "vid1"}], "json", str(out))

        assert out.exists()

    def test_accepts_path_objects(self, tmp_path):
        out = tmp_path / "meta.json"

        path = export_metadata([{"id": "vid1"}], "json", out)

        assert path == str(out)


class TestExportMetadataRejectsUnknownFormat:
    @pytest.mark.parametrize("fmt", ["xml", "yaml", "", "CSV", "tsv"])
    def test_raises_value_error_with_legacy_message(self, tmp_path, fmt):
        with pytest.raises(ValueError, match="Format tidak didukung"):
            export_metadata([{"id": "vid1"}], fmt, str(tmp_path / "out.dat"))

    def test_no_file_is_created_on_rejection(self, tmp_path):
        out = tmp_path / "out.dat"

        with pytest.raises(ValueError):
            export_metadata([{"id": "vid1"}], "xml", str(out))

        assert not out.exists()

    def test_format_is_case_sensitive_like_legacy(self, tmp_path):
        with pytest.raises(ValueError):
            export_metadata([{"id": "vid1"}], "CSV", str(tmp_path / "out.csv"))
