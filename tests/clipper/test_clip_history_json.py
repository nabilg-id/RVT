"""Tests for the JSON clip history.

``clip-history.log`` was a pipe-delimited line per clip job, and the pipe was
load-bearing: a title containing one had to be rewritten to a slash before it
could be stored, and a title longer than sixty characters was cut. So the one
field a person reads to identify a clip was also the one most likely to be
mangled, and there was no way back to the original.

``clip-history.jsonl`` stores the record as it actually is. The API keeps its
shape, because ``app.js`` renders it, but the file no longer has to fit on a
line.

The legacy file is still read. Existing installs have one, and a history that
starts empty because the format changed is worse than one that is awkward to
parse.
"""
from __future__ import annotations

import json

import pytest

from clipper import app as A

LEGACY_FILE = "clip-history.log"


@pytest.fixture(autouse=True)
def _temp_dir(tmp_path, monkeypatch):
    """Point the history at a scratch folder.

    Without this the writer appends to the real temp directory, which both
    litters the repo and makes these tests order-dependent - an existing
    history file would show up as extra rows.
    """
    monkeypatch.setattr(A, "TEMP_DIR", tmp_path)
    return tmp_path


class _Stub:
    def __init__(self, outputs=(), status="done", title="Judul", video_id=None):
        self.outputs = list(outputs)
        self.status = status
        self.title = title
        self.video_id = video_id


def _append(**overrides):
    payload = {"style": "clean_white", "minDur": 20, "maxDur": 60}
    payload.update(overrides)
    job = _Stub(**{k: v for k, v in overrides.items()
                   if k in ("outputs", "status", "title", "video_id")})
    A._append_history(payload, job)
    return job


def _records():
    path = A.TEMP_DIR / A._RCH_HISTORY_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestWritesJson:
    def test_the_file_is_json_lines(self):
        _append()

        assert A._RCH_HISTORY_FILE == "clip-history.jsonl"
        assert len(_records()) == 1

    def test_no_legacy_line_is_written(self):
        _append()

        assert not (A.TEMP_DIR / LEGACY_FILE).exists()

    def test_a_pipe_in_the_title_is_stored_verbatim(self):
        """The old writer replaced every pipe with a slash, so the title on
        screen was never the title of the video."""
        _append(title="A | B")

        assert _records()[0]["title"] == "A | B"

    def test_a_long_title_is_not_truncated(self):
        title = "Judul yang jauh lebih panjang daripada enam puluh karakter untuk memastikan tidak dipotong"

        _append(title=title)

        assert _records()[0]["title"] == title

    def test_counts_are_numbers(self):
        _append(outputs=["a.mp4", "b.mp4", "c.mp4"])

        record = _records()[0]
        assert record["clips"] == 3
        assert record["minDur"] == 20
        assert record["maxDur"] == 60

    def test_the_video_id_travels_with_the_record(self):
        _append(video_id="dQw4w9WgXcQ")

        assert _records()[0]["videoId"] == "dQw4w9WgXcQ"

    def test_the_produced_files_are_recorded(self):
        """The history row said how many clips there were but never which ones,
        so a finished job could not be matched to its output."""
        _append(outputs=["clip_1_88pts_abc.mp4", "clip_2_88pts_abc.mp4"])

        assert _records()[0]["files"] == ["clip_1_88pts_abc.mp4",
                                          "clip_2_88pts_abc.mp4"]

    def test_a_failed_job_is_recorded_with_no_files(self):
        _append(status="error", title=None, outputs=[])

        record = _records()[0]
        assert record["status"] == "error"
        assert record["files"] == []

    def test_it_records_when_it_ran(self):
        _append()

        assert _records()[0]["timestamp"]

    def test_each_run_is_its_own_line(self):
        _append(title="satu")
        _append(title="dua")

        records = _records()
        assert len(records) == 2
        assert [r["title"] for r in records] == ["satu", "dua"]

    def test_a_newline_in_a_title_cannot_split_the_line(self):
        """The other half of why this moved to JSON: a title with a newline
        would have become two records."""
        _append(title="Baris satu\nBaris dua")

        records = _records()
        assert len(records) == 1
        assert records[0]["title"] == "Baris satu\nBaris dua"


class TestApiShape:
    def test_the_reader_still_returns_what_the_table_renders(self):
        _append(title="Judul Video", video_id="dQw4w9WgXcQ",
                outputs=["a.mp4"])

        row = A.read_history()[0]
        assert set(row) == {"timestamp", "style", "clips", "range",
                            "status", "title", "videoId"}
        assert row["range"] == "20–60s"
        assert row["clips"] == 1

    def test_the_reader_returns_newest_first(self):
        _append(title="lama")
        _append(title="baru")

        assert [r["title"] for r in A.read_history()] == ["baru", "lama"]

    def test_the_limit_still_applies(self):
        for i in range(10):
            _append(title=f"T{i}")

        assert len(A.read_history(limit=3)) == 3


class TestLegacyIsStillReadable:
    def _write(self, tmp_path, text):
        (tmp_path / LEGACY_FILE).write_text(text, encoding="utf-8")

    def test_a_seven_field_line_reads(self):
        self._write(A.TEMP_DIR,
                    "2026-01-01 00:00:00 | clean_white | clips=2 | min=10 | max=20"
                    " | done | Judul Lama\n")

        row = A.read_history()[0]
        assert row["videoId"] is None
        assert row["title"] == "Judul Lama"
        assert row["clips"] == 2
        assert row["range"] == "10–20s"

    def test_an_eighth_field_carries_the_id(self):
        self._write(A.TEMP_DIR,
                    "2026-01-01 00:00:00 | clean_white | clips=1 | min=10 | max=20"
                    " | done | Judul | id=dQw4w9WgXcQ\n")

        assert A.read_history()[0]["videoId"] == "dQw4w9WgXcQ"

    def test_a_placeholder_id_is_not_reported(self):
        self._write(A.TEMP_DIR,
                    "2026-01-01 00:00:00 | clean_white | clips=1 | min=10 | max=20"
                    " | error | Gagal | id=-\n")

        assert A.read_history()[0]["videoId"] is None

    def test_a_short_line_is_skipped(self):
        self._write(A.TEMP_DIR, "terlalu | pendek\n")

        assert A.read_history() == []

    def test_legacy_entries_come_before_new_ones(self):
        """A run that migrates must not look like the older clips never
        happened."""
        self._write(A.TEMP_DIR,
                    "2026-01-01 00:00:00 | clean_white | clips=1 | min=1 | max=2"
                    " | done | Versi Lama\n")
        _append(title="Versi Baru")

        titles = [r["title"] for r in reversed(A.read_history())]
        assert titles == ["Versi Lama", "Versi Baru"]

    def test_an_empty_folder_reads_as_empty(self):
        assert A.read_history() == []


class TestRobustness:
    def test_a_corrupt_json_line_does_not_hide_the_rest(self):
        _append(title="sebelum")
        with open(A.TEMP_DIR / A._RCH_HISTORY_FILE, "a", encoding="utf-8") as fh:
            fh.write("{bukan json\n")
        _append(title="sesudah")

        assert [r["title"] for r in reversed(A.read_history())] == ["sebelum",
                                                                    "sesudah"]

    def test_an_unwritable_history_does_not_break_the_job(self, monkeypatch, tmp_path):
        """History is a convenience. Losing it must never cost the user a clip,
        so the write is best-effort by design.

        A file standing where a directory is needed makes mkdir fail for real,
        which beats stubbing the single call it makes and hoping it is the only
        one.
        """
        blocker = tmp_path / "bukan-direktori"
        blocker.write_text("aku file", encoding="utf-8")
        monkeypatch.setattr(A, "TEMP_DIR", blocker / "temp")

        _append(title="tidak tersimpan")  # must not raise

        assert A.read_history() == []

    def test_a_record_missing_optional_fields_still_renders(self):
        """A hand-edited or partially written line should show something rather
        than raise inside a request."""

        (A.TEMP_DIR / A._RCH_HISTORY_FILE).write_text(
            json.dumps({"timestamp": "t", "style": "s"}) + "\n",
            encoding="utf-8",
        )

        row = A.read_history()[0]
        assert row["clips"] == 0
        assert row["videoId"] is None
