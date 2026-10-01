"""Tests for rch.youtube.metadata — batch metadata collection.

Ported from ``legacy-node/lib/youtube/metadata.js``. Two boundaries are
injected so nothing here touches the network or spawns a process: the yt-dlp
runner (``run_ytdlp``) and the HTTP getter (``http_get``).

The batch path is the production hot spot: one yt-dlp invocation per 30 ids,
up to 3 in parallel, printed fields separated by a sentinel. Getting the
sentinel parsing wrong silently drops or mis-attributes descriptions, so the
parser is asserted exhaustively against the exact ``--print`` layout yt-dlp
emits.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

import pytest

from rch.youtube.metadata import (
    DELIM,
    default_run_ytdlp,
    ensure_ytdlp_updated,
    get_channel_ids,
    get_video_info,
    get_video_info_batch,
    get_video_info_fallback,
    normalize_video_id,
    parse_batch_output,
    thumbnail_url,
    watch_url,
)

_VIDEO_ID = "dQw4w9WgXcQ"
_THUMB = "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
_WATCH = "https://youtu.be/dQw4w9WgXcQ"


def _block(*lines):
    """Render one yt-dlp record exactly as the ``--print`` sequence emits it."""
    return DELIM + "\n" + "\n".join(lines) + "\n"


def _urls_per_call(args):
    """Count the watch URLs trailing one batch invocation's argument list."""
    return sum(1 for arg in args if arg.startswith("https://youtu.be/"))


class ConcurrencyProbe:
    """Runner that measures how many invocations overlap in time."""

    def __init__(self, hold=0.05):
        self.hold = hold
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.total = 0

    def __call__(self, args, **kwargs):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.total += 1
        time.sleep(self.hold)
        with self._lock:
            self.active -= 1
        return ""


class FakeRunner:
    """Replays scripted stdout per invocation and records the args it received."""

    def __init__(self, scripts=None, default=""):
        self.scripts = list(scripts or [])
        self.default = default
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.scripts:
            return self.scripts[min(len(self.calls) - 1, len(self.scripts) - 1)]
        return self.default

    @property
    def urls(self):
        """Every URL passed across all invocations, flattened."""
        return [a for args, _ in self.calls for a in args if a.startswith("https://")]


class TestConstants:
    def test_delimiter_is_the_legacy_sentinel(self):
        assert DELIM == "<<RCH_SPLIT>>"


class TestUrlHelpers:
    def test_watch_url_shape(self):
        assert watch_url(_VIDEO_ID) == "https://youtu.be/dQw4w9WgXcQ"

    def test_thumbnail_url_shape(self):
        assert thumbnail_url(_VIDEO_ID) == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


class TestNormalizeVideoId:
    def test_bare_id_passes_through(self):
        assert normalize_video_id(_VIDEO_ID) == _VIDEO_ID

    def test_bare_id_is_trimmed(self):
        assert normalize_video_id(f"  {_VIDEO_ID}  ") == _VIDEO_ID

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
        ],
    )
    def test_urls_are_reduced_to_their_id(self, url):
        assert normalize_video_id(url) == _VIDEO_ID

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "tooshort", "waytoolongvideoid", "https://example.invalid/x", None, 123, []],
    )
    def test_unusable_values_return_none(self, value):
        assert normalize_video_id(value) is None

    def test_id_with_url_unsafe_characters_returns_none(self):
        assert normalize_video_id("../../etc/passwd") is None

    def test_non_string_returns_none(self):
        assert normalize_video_id(None) is None
        assert normalize_video_id(42) is None


class TestParseBatchOutput:
    def test_empty_output_yields_no_records(self):
        assert parse_batch_output("") == []

    def test_leading_text_before_first_delimiter_is_discarded(self):
        out = "WARNING: something noisy\n" + _block(_VIDEO_ID, "Judul", "212", "20260101")

        records = parse_batch_output(out)

        assert len(records) == 1
        assert records[0]["id"] == _VIDEO_ID

    def test_extracts_all_metadata_fields(self):
        out = _block(_VIDEO_ID, "Judul Video", "212", "20260101", "Deskripsi")

        records = parse_batch_output(out)

        assert records == [{
            "id": _VIDEO_ID,
            "title": "Judul Video",
            "duration": 212,
            "uploadDate": "20260101",
            "description": "Deskripsi",
            "thumbnail": _THUMB,
            "url": _WATCH,
        }]

    def test_parses_multiple_records(self):
        out = _block("vid00000001", "Satu", "10", "20260101", "D1") + _block(
            "vid00000002", "Dua", "20", "20260102", "D2"
        )

        records = parse_batch_output(out)

        assert [r["id"] for r in records] == ["vid00000001", "vid00000002"]
        assert [r["duration"] for r in records] == [10, 20]

    def test_multiline_description_is_rejoined_with_newlines(self):
        out = _block(_VIDEO_ID, "Judul", "212", "20260101", "baris 1", "baris 2", "baris 3")

        records = parse_batch_output(out)

        assert records[0]["description"] == "baris 1\nbaris 2\nbaris 3"

    def test_blank_lines_inside_description_are_preserved(self):
        out = _block(_VIDEO_ID, "Judul", "212", "20260101", "baris 1", "", "baris 2")

        records = parse_batch_output(out)

        assert records[0]["description"] == "baris 1\n\nbaris 2"

    def test_trailing_blank_lines_are_trimmed_from_description(self):
        out = _block(_VIDEO_ID, "Judul", "212", "20260101", "baris 1", "", "")

        assert parse_batch_output(out)[0]["description"] == "baris 1"

    def test_description_lines_are_trimmed(self):
        out = _block(_VIDEO_ID, "Judul", "212", "20260101", "   baris 1   ", "\tbaris 2\t")

        records = parse_batch_output(out)

        assert records[0]["description"] == "baris 1\nbaris 2"

    def test_empty_title_falls_back_to_id(self):
        out = _block(_VIDEO_ID, "", "212", "20260101", "")

        records = parse_batch_output(out)

        assert records[0]["title"] == _VIDEO_ID

    def test_empty_title_does_not_shift_later_fields(self):
        out = _block(_VIDEO_ID, "", "212", "20260101", "Deskripsi")

        record = parse_batch_output(out)[0]
        assert record["title"] == _VIDEO_ID
        assert record["duration"] == 212
        assert record["uploadDate"] == "20260101"
        assert record["description"] == "Deskripsi"

    def test_empty_upload_date_does_not_shift_description(self):
        out = _block(_VIDEO_ID, "Judul", "212", "", "Deskripsi")

        record = parse_batch_output(out)[0]
        assert record["uploadDate"] is None
        assert record["description"] == "Deskripsi"

    def test_record_with_only_an_id_fills_defaults(self):
        out = _block(_VIDEO_ID)

        record = parse_batch_output(out)[0]
        assert record["title"] == _VIDEO_ID
        assert record["duration"] is None
        assert record["uploadDate"] is None
        assert record["description"] == ""

    def test_non_numeric_duration_becomes_none(self):
        out = _block(_VIDEO_ID, "Judul", "NA", "20260101", "")

        assert parse_batch_output(out)[0]["duration"] is None

    def test_zero_duration_becomes_none(self):
        out = _block(_VIDEO_ID, "Judul", "0", "20260101", "")

        assert parse_batch_output(out)[0]["duration"] is None

    def test_float_duration_is_truncated_to_int(self):
        out = _block(_VIDEO_ID, "Judul", "212.75", "20260101", "")

        assert parse_batch_output(out)[0]["duration"] == 212

    def test_missing_duration_becomes_none(self):
        out = _block(_VIDEO_ID, "Judul", "", "20260101", "")

        assert parse_batch_output(out)[0]["duration"] is None

    def test_missing_upload_date_becomes_none(self):
        out = _block(_VIDEO_ID, "Judul", "212", "", "")

        assert parse_batch_output(out)[0]["uploadDate"] is None

    def test_missing_description_becomes_empty_string(self):
        out = _block(_VIDEO_ID, "Judul", "212", "20260101")

        assert parse_batch_output(out)[0]["description"] == ""

    def test_record_without_id_is_skipped(self):
        out = _block("", "Judul Tanpa Id", "212", "20260101", "")

        assert parse_batch_output(out) == []

    def test_delimiter_only_block_is_skipped(self):
        out = DELIM + "\n" + "\n"

        assert parse_batch_output(out) == []

    def test_thumbnail_and_url_are_derived_from_id(self):
        out = _block("vid00000042", "Judul", "10", "20260101", "")

        record = parse_batch_output(out)[0]
        assert record["thumbnail"] == "https://i.ytimg.com/vi/vid00000042/hqdefault.jpg"
        assert record["url"] == "https://youtu.be/vid00000042"

    def test_output_without_any_delimiter_yields_nothing(self):
        assert parse_batch_output("vid00000001\nJudul\n212\n") == []

    def test_crlf_output_is_parsed(self):
        out = (DELIM + "\r\n" + "vid00000001\r\nJudul\r\n212\r\n20260101\r\nDeskripsi\r\n")

        records = parse_batch_output(out)

        assert records[0]["id"] == "vid00000001"
        assert records[0]["title"] == "Judul"
        assert records[0]["duration"] == 212

    def test_unicode_titles_survive(self):
        out = _block(_VIDEO_ID, "Halo Dunia — Änderung 日本語", "212", "20260101", "")

        assert parse_batch_output(out)[0]["title"] == "Halo Dunia — Änderung 日本語"


class TestGetVideoInfoBatch:
    def test_returns_mapping_keyed_by_video_id(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "Deskripsi")])

        result = get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)

        assert set(result) == {_VIDEO_ID}
        assert result[_VIDEO_ID]["title"] == "Judul"
        assert result[_VIDEO_ID]["duration"] == 212
        assert result[_VIDEO_ID]["uploadDate"] == "20260101"
        assert result[_VIDEO_ID]["description"] == "Deskripsi"

    def test_empty_input_returns_empty_mapping_without_calling_ytdlp(self):
        runner = FakeRunner()

        assert get_video_info_batch([], run_ytdlp=runner) == {}
        assert runner.calls == []

    def test_uses_legacy_yt_dlp_flags(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)

        args = runner.calls[0][0]
        assert args[:6] == [
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            "--print",
            DELIM,
            "--print",
        ]
        assert "%(id)s" in args
        assert "%(title)s" in args
        assert "%(duration)s" in args
        assert "%(upload_date)s" in args
        assert "%(description)s" in args

    def test_field_print_order_matches_legacy(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)

        args = runner.calls[0][0]
        assert args[args.index("--print", 5) + 1] == "%(id)s"
        printed = [args[i + 1] for i, a in enumerate(args) if a == "--print"]
        assert printed == [DELIM, "%(id)s", "%(title)s", "%(duration)s", "%(upload_date)s",
                           "%(description)s"]

    def test_ids_are_expanded_to_watch_urls(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)

        assert runner.urls == [_WATCH]

    def test_accepts_full_urls_as_input(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        result = get_video_info_batch([f"https://www.youtube.com/watch?v={_VIDEO_ID}"],
                                      run_ytdlp=runner)

        assert set(result) == {_VIDEO_ID}
        assert runner.urls == [_WATCH]

    def test_chunks_at_thirty_ids_by_default(self):
        ids = [f"vid{i:08d}" for i in range(65)]
        runner = FakeRunner([""])

        get_video_info_batch(ids, run_ytdlp=runner)

        assert [_urls_per_call(args) for args, _ in runner.calls] == [30, 30, 5]

    def test_chunk_size_is_configurable(self):
        ids = [f"vid{i:08d}" for i in range(5)]
        runner = FakeRunner([""])

        get_video_info_batch(ids, run_ytdlp=runner, chunk_size=2)

        assert [_urls_per_call(args) for args, _ in runner.calls] == [2, 2, 1]

    def test_every_id_is_requested_exactly_once(self):
        ids = [f"vid{i:08d}" for i in range(7)]
        runner = FakeRunner([""])

        get_video_info_batch(ids, run_ytdlp=runner, chunk_size=3)

        assert sorted(runner.urls) == sorted(f"https://youtu.be/{i}" for i in ids)

    def test_large_input_is_split_into_four_chunks(self):
        ids = [f"vid{i:08d}" for i in range(120)]
        runner = FakeRunner([""])

        get_video_info_batch(ids, run_ytdlp=runner)

        assert len(runner.calls) == 4

    def test_chunks_are_fetched_concurrently(self):
        probe = ConcurrencyProbe()
        ids = [f"vid{i:08d}" for i in range(6)]

        get_video_info_batch(ids, run_ytdlp=probe, chunk_size=1, max_workers=3)

        assert probe.peak > 1

    def test_never_exceeds_three_parallel_workers(self):
        probe = ConcurrencyProbe()
        ids = [f"vid{i:08d}" for i in range(12)]

        get_video_info_batch(ids, run_ytdlp=probe, chunk_size=1)

        assert probe.peak <= 3

    def test_max_workers_is_configurable(self):
        probe = ConcurrencyProbe()
        ids = [f"vid{i:08d}" for i in range(6)]

        get_video_info_batch(ids, run_ytdlp=probe, chunk_size=1, max_workers=1)

        assert probe.peak == 1

    def test_failure_of_one_chunk_does_not_abort_the_others(self):
        ids = [f"vid{i:08d}" for i in range(4)]
        good = _block("vid00000003", "Tiga", "30", "20260103", "")

        def runner(args, **kwargs):
            if args[-1].endswith("vid00000001"):
                raise RuntimeError("yt-dlp exploded")
            return good

        result = get_video_info_batch(ids, run_ytdlp=runner, chunk_size=1)

        assert "vid00000003" in result

    def test_total_failure_yields_empty_mapping(self):
        def runner(args, **kwargs):
            raise RuntimeError("yt-dlp exploded")

        assert get_video_info_batch([_VIDEO_ID], run_ytdlp=runner) == {}

    def test_malformed_output_is_ignored(self):
        runner = FakeRunner(["not json, not delimited, nonsense"])

        assert get_video_info_batch([_VIDEO_ID], run_ytdlp=runner) == {}

    def test_invalid_entries_are_skipped(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        result = get_video_info_batch([_VIDEO_ID, "", "not-an-id", None], run_ytdlp=runner)

        assert set(result) == {_VIDEO_ID}
        assert runner.urls == [_WATCH]

    def test_duplicate_ids_yield_a_single_entry(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        result = get_video_info_batch([_VIDEO_ID, _VIDEO_ID], run_ytdlp=runner)

        assert len(result) == 1

    def test_ensure_updated_is_invoked_before_fetching(self):
        events = []
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        def ensure():
            events.append("update")

        def runner_wrapper(args, **kwargs):
            events.append("fetch")
            return runner(args, **kwargs)

        get_video_info_batch([_VIDEO_ID], run_ytdlp=runner_wrapper, ensure_updated=ensure)

        assert events[0] == "update"

    def test_ytdlp_timeout_matches_legacy_default(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "")])

        get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)

        assert runner.calls[0][1]["timeout"] == 90000

    def test_chunk_results_are_merged_across_invocations(self):
        ids = ["vid00000001", "vid00000002"]
        outputs = [
            _block("vid00000001", "Satu", "10", "20260101", "D1"),
            _block("vid00000002", "Dua", "20", "20260102", "D2"),
        ]
        calls = {"n": 0}

        def runner(args, **kwargs):
            index = calls["n"]
            calls["n"] += 1
            return outputs[index]

        result = get_video_info_batch(ids, run_ytdlp=runner, chunk_size=1)

        assert set(result) == {"vid00000001", "vid00000002"}
        assert result["vid00000002"]["title"] == "Dua"

    def test_result_contains_the_five_consumer_fields(self):
        runner = FakeRunner([_block(_VIDEO_ID, "Judul", "212", "20260101", "Deskripsi")])

        record = get_video_info_batch([_VIDEO_ID], run_ytdlp=runner)[_VIDEO_ID]

        for field in ("title", "description", "duration", "uploadDate", "url"):
            assert field in record


class TestGetVideoInfo:
    def _payload(self, **overrides):
        meta = {
            "id": _VIDEO_ID,
            "title": "Judul Lengkap",
            "description": "Deskripsi lengkap",
            "duration": 212,
            "thumbnail": "https://i.ytimg.com/vi/%s/maxresdefault.jpg" % _VIDEO_ID,
        }
        meta.update(overrides)
        return json.dumps(meta)

    def test_returns_metadata_record(self):
        runner = FakeRunner([self._payload()])

        record = get_video_info(f"https://youtu.be/{_VIDEO_ID}", run_ytdlp=runner)

        assert record["id"] == _VIDEO_ID
        assert record["title"] == "Judul Lengkap"
        assert record["description"] == "Deskripsi lengkap"
        assert record["duration"] == 212

    def test_uses_dump_single_json_flags(self):
        runner = FakeRunner([self._payload()])

        get_video_info(f"https://youtu.be/{_VIDEO_ID}", run_ytdlp=runner)

        assert runner.calls[0][0] == [
            "--dump-single-json",
            "--no-playlist",
            f"https://youtu.be/{_VIDEO_ID}",
        ]

    def test_target_is_passed_through_verbatim(self):
        runner = FakeRunner([self._payload()])

        get_video_info(_VIDEO_ID, run_ytdlp=runner)

        assert runner.calls[0][0][-1] == _VIDEO_ID

    def test_falls_back_to_default_thumbnail(self):
        runner = FakeRunner([self._payload(thumbnail="")])

        record = get_video_info(_VIDEO_ID, run_ytdlp=runner)

        assert record["thumbnail"] == _THUMB

    def test_keeps_reported_thumbnail(self):
        runner = FakeRunner([self._payload()])

        record = get_video_info(_VIDEO_ID, run_ytdlp=runner)

        assert record["thumbnail"].endswith("maxresdefault.jpg")

    def test_title_falls_back_to_id(self):
        runner = FakeRunner([self._payload(title="")])

        assert get_video_info(_VIDEO_ID, run_ytdlp=runner)["title"] == _VIDEO_ID

    def test_description_falls_back_to_empty_string(self):
        runner = FakeRunner([self._payload(description="")])

        assert get_video_info(_VIDEO_ID, run_ytdlp=runner)["description"] == ""

    def test_missing_duration_becomes_none(self):
        runner = FakeRunner([self._payload(duration=None)])

        assert get_video_info(_VIDEO_ID, run_ytdlp=runner)["duration"] is None

    def test_zero_duration_becomes_none(self):
        runner = FakeRunner([self._payload(duration=0)])

        assert get_video_info(_VIDEO_ID, run_ytdlp=runner)["duration"] is None

    def test_url_is_derived_from_id(self):
        runner = FakeRunner([self._payload()])

        assert get_video_info(_VIDEO_ID, run_ytdlp=runner)["url"] == _WATCH

    def test_ensure_updated_runs_before_fetch(self):
        events = []
        runner = FakeRunner([self._payload()])

        get_video_info(
            _VIDEO_ID,
            run_ytdlp=lambda args, **kw: (events.append("fetch"), self._payload())[1],
            ensure_updated=lambda: events.append("update"),
        )

        assert events == ["update", "fetch"]
        assert runner.calls == []

    def test_ytdlp_failure_propagates(self):
        def runner(args, **kwargs):
            raise RuntimeError("yt-dlp exploded")

        with pytest.raises(RuntimeError, match="yt-dlp exploded"):
            get_video_info(_VIDEO_ID, run_ytdlp=runner)

    def test_invalid_json_raises_runtime_error(self):
        runner = FakeRunner(["<html>not json</html>"])

        with pytest.raises(RuntimeError):
            get_video_info(_VIDEO_ID, run_ytdlp=runner)

    def test_empty_output_raises_runtime_error(self):
        runner = FakeRunner([""])

        with pytest.raises(RuntimeError):
            get_video_info(_VIDEO_ID, run_ytdlp=runner)

    def test_json_array_instead_of_object_raises(self):
        runner = FakeRunner(["[]"])

        with pytest.raises(RuntimeError):
            get_video_info(_VIDEO_ID, run_ytdlp=runner)

    def test_json_null_raises(self):
        runner = FakeRunner(["null"])

        with pytest.raises(RuntimeError):
            get_video_info(_VIDEO_ID, run_ytdlp=runner)


class TestGetChannelIds:
    def test_returns_list_of_ids(self):
        runner = FakeRunner(["vid00000001\nvid00000002\nvid00000003\n"])

        assert get_channel_ids("@channel", run_ytdlp=runner) == [
            "vid00000001",
            "vid00000002",
            "vid00000003",
        ]

    def test_uses_flat_playlist_flags(self):
        runner = FakeRunner(["vid00000001\n"])

        get_channel_ids("@channel", run_ytdlp=runner)

        assert runner.calls[0][0] == [
            "--flat-playlist",
            "--print",
            "%(id)s",
            "@channel",
        ]

    def test_trims_whitespace_and_drops_blank_lines(self):
        runner = FakeRunner(["  vid00000001  \n\n\tvid00000002\t\n\n"])

        assert get_channel_ids("@channel", run_ytdlp=runner) == [
            "vid00000001",
            "vid00000002",
        ]

    def test_handles_crlf_output(self):
        runner = FakeRunner(["vid00000001\r\nvid00000002\r\n"])

        assert get_channel_ids("@channel", run_ytdlp=runner) == [
            "vid00000001",
            "vid00000002",
        ]

    def test_empty_output_returns_empty_list(self):
        assert get_channel_ids("@channel", run_ytdlp=FakeRunner([""])) == []

    def test_blank_only_output_returns_empty_list(self):
        assert get_channel_ids("@channel", run_ytdlp=FakeRunner(["\n \n\n"])) == []

    def test_ensure_updated_runs_before_fetch(self):
        events = []

        get_channel_ids(
            "@channel",
            run_ytdlp=lambda args, **kw: (events.append("fetch"), "vid00000001\n")[1],
            ensure_updated=lambda: events.append("update"),
        )

        assert events == ["update", "fetch"]

    def test_ytdlp_failure_propagates(self):
        def runner(args, **kwargs):
            raise RuntimeError("yt-dlp exploded")

        with pytest.raises(RuntimeError, match="yt-dlp exploded"):
            get_channel_ids("@channel", run_ytdlp=runner)

    @pytest.mark.parametrize("bad_url", ["", None])
    def test_missing_channel_url_raises_without_calling_ytdlp(self, bad_url):
        runner = FakeRunner(["vid00000001\n"])

        with pytest.raises(ValueError):
            get_channel_ids(bad_url, run_ytdlp=runner)

        assert runner.calls == []


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestDefaultRunYtdlp:
    def test_returns_stdout(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs
            return FakeProc(stdout="output text")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert default_run_ytdlp(["--print", "%(id)s"]) == "output text"
        assert seen["cmd"][0] == "yt-dlp"
        assert seen["cmd"][1:] == ["--print", "%(id)s"] or seen["cmd"][-2:] == [
            "--print",
            "%(id)s",
        ]

    def test_mitigation_flags_are_prepended(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return FakeProc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        default_run_ytdlp(["--no-playlist"], no_sleep=True)

        assert "--no-playlist" in seen["cmd"]
        assert "--user-agent" in seen["cmd"]

    def test_no_sleep_suppresses_sleep_flags(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return FakeProc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        default_run_ytdlp(["--no-playlist"], no_sleep=True)

        assert "--sleep-requests" not in seen["cmd"]

    def test_cookies_flag_is_added(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return FakeProc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        default_run_ytdlp(["--no-playlist"], cookies="chrome")

        assert "--cookies-from-browser" in seen["cmd"]

    def test_timeout_is_converted_from_milliseconds_to_seconds(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["kwargs"] = kwargs
            return FakeProc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        default_run_ytdlp(["--no-playlist"], timeout=90000)

        assert seen["kwargs"]["timeout"] == 90.0

    def test_no_timeout_kwarg_when_not_requested(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["kwargs"] = kwargs
            return FakeProc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        default_run_ytdlp(["--no-playlist"])

        assert "timeout" not in seen["kwargs"]

    def test_none_stdout_becomes_empty_string(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeProc(stdout=None))

        assert default_run_ytdlp(["--no-playlist"]) == ""

    def test_non_zero_exit_raises_with_stderr(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: FakeProc(returncode=1, stderr="boom detail")
        )

        with pytest.raises(RuntimeError, match="boom detail"):
            default_run_ytdlp(["--no-playlist"])

    def test_non_zero_exit_without_stderr_uses_exit_code(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: FakeProc(returncode=3, stderr="")
        )

        with pytest.raises(RuntimeError, match="yt-dlp exited with 3"):
            default_run_ytdlp(["--no-playlist"])

    def test_missing_binary_raises_actionable_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("yt-dlp")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="yt-dlp tidak ditemukan"):
            default_run_ytdlp(["--no-playlist"])


class TestEnsureYtdlpUpdated:
    def test_invokes_self_update(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return FakeProc()

        monkeypatch.setattr(subprocess, "run", fake_run)

        ensure_ytdlp_updated()

        assert seen["cmd"] == ["yt-dlp", "-U"]

    def test_swallows_failure(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("yt-dlp")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ensure_ytdlp_updated() is None

    def test_swallows_timeout(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ensure_ytdlp_updated() is None


class ScriptedHttp:
    """Serves scripted bodies per URL and records every request."""

    def __init__(self, by_url=None, default=""):
        self.by_url = dict(by_url or {})
        self.default = default
        self.requested = []

    def __call__(self, url):
        self.requested.append(url)
        outcome = self.by_url.get(url, self.default)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


_OEMBED = "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=%s&format=json" % _VIDEO_ID
_WATCH_PAGE = "https://www.youtube.com/watch?v=%s" % _VIDEO_ID


def _oembed_body(title="Judul oEmbed"):
    return json.dumps({"title": title, "author_name": "Kanal"})


class TestGetVideoInfoFallbackViaOembed:
    def test_returns_title_from_oembed(self):
        http = ScriptedHttp({_OEMBED: _oembed_body()})

        record = get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert record["title"] == "Judul oEmbed"

    def test_record_shape_matches_legacy(self):
        http = ScriptedHttp({_OEMBED: _oembed_body()})

        record = get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert record == {
            "id": _VIDEO_ID,
            "title": "Judul oEmbed",
            "description": "",
            "thumbnail": _THUMB,
            "duration": None,
            "url": _WATCH,
        }

    def test_hits_the_oembed_endpoint_first(self):
        http = ScriptedHttp({_OEMBED: _oembed_body()})

        get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert http.requested[0] == _OEMBED

    def test_does_not_fetch_watch_page_when_oembed_succeeds(self):
        http = ScriptedHttp({_OEMBED: _oembed_body()})

        get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert _WATCH_PAGE not in http.requested


class TestGetVideoInfoFallbackViaWatchPage:
    def test_extracts_title_and_strips_youtube_suffix(self):
        http = ScriptedHttp({_OEMBED: "", _WATCH_PAGE: "<html><head><title>Judul Video - YouTube</title></head></html>"})

        record = get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert record["title"] == "Judul Video"

    def test_meta_title_tag_is_preferred(self):
        html = (
            '<html><head><meta name="title" content="Dari Meta">'
            "<title>Dari Title Tag - YouTube</title></head></html>"
        )
        http = ScriptedHttp({_OEMBED: "", _WATCH_PAGE: html})

        assert get_video_info_fallback(_VIDEO_ID, http_get=http)["title"] == "Dari Meta"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Judul - YouTube", "Judul"),
            ("Judul - youtube", "Judul"),
            ("Judul - YOUTUBE", "Judul"),
            ("Judul   -   YouTube   ", "Judul"),
            ("Judul - YouTube\n", "Judul"),
            ("Judul", "Judul"),
            ("Judul (Official Video)", "Judul (Official Video)"),
        ],
    )
    def test_title_suffix_normalisation(self, raw, expected):
        http = ScriptedHttp({_OEMBED: "", _WATCH_PAGE: f"<title>{raw}</title>"})

        assert get_video_info_fallback(_VIDEO_ID, http_get=http)["title"] == expected

    def test_falls_through_when_oembed_fails(self):
        http = ScriptedHttp({_OEMBED: RuntimeError("network down"),
                             _WATCH_PAGE: "<title>Judul Halaman - YouTube</title>"})

        record = get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert record["title"] == "Judul Halaman"

    def test_falls_through_when_oembed_json_is_invalid(self):
        http = ScriptedHttp({_OEMBED: "<html>captcha</html>",
                             _WATCH_PAGE: "<title>Judul Halaman - YouTube</title>"})

        assert get_video_info_fallback(_VIDEO_ID, http_get=http)["title"] == "Judul Halaman"

    def test_falls_through_when_oembed_has_no_title(self):
        http = ScriptedHttp({_OEMBED: json.dumps({"author_name": "Kanal"}),
                             _WATCH_PAGE: "<title>Judul Halaman - YouTube</title>"})

        assert get_video_info_fallback(_VIDEO_ID, http_get=http)["title"] == "Judul Halaman"

    def test_record_from_watch_page_has_empty_description_and_no_duration(self):
        http = ScriptedHttp({_OEMBED: "", _WATCH_PAGE: "<title>Judul - YouTube</title>"})

        record = get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert record["description"] == ""
        assert record["duration"] is None
        assert record["thumbnail"] == _THUMB
        assert record["url"] == _WATCH

    def test_watch_page_is_only_requested_after_oembed(self):
        http = ScriptedHttp({_OEMBED: "", _WATCH_PAGE: "<title>Judul - YouTube</title>"})

        get_video_info_fallback(_VIDEO_ID, http_get=http)

        assert http.requested == [_OEMBED, _WATCH_PAGE]


class TestGetVideoInfoFallbackTotalFailure:
    def test_returns_none_when_both_sources_fail(self):
        http = ScriptedHttp(default=RuntimeError("network down"))

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None

    def test_returns_none_for_empty_html(self):
        http = ScriptedHttp(default="")

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None

    def test_returns_none_for_html_without_any_title(self):
        http = ScriptedHttp(default="<html><body><p>Nothing here</p></body></html>")

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None

    def test_returns_none_for_blank_title_tag(self):
        http = ScriptedHttp(default="<html><head><title>   </title></head></html>")

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None

    def test_returns_none_for_title_that_is_only_the_youtube_suffix(self):
        http = ScriptedHttp(default="<title>- YouTube</title>")

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None

    def test_returns_none_for_og_title_only_page(self):
        http = ScriptedHttp(
            default='<meta property="og:title" content="Judul">'
        )

        assert get_video_info_fallback(_VIDEO_ID, http_get=http) is None


class TestGetVideoInfoFallbackDefaultHttp:
    def test_uses_core_http_with_browser_user_agent(self, monkeypatch):
        seen = {}

        class Resp:
            text = _oembed_body()

        def fake_http_get(url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return Resp()

        monkeypatch.setattr("rch.core.http.http_get", fake_http_get)

        record = get_video_info_fallback(_VIDEO_ID)

        assert record["id"] == _VIDEO_ID
        assert seen["url"] == _OEMBED
        assert "User-Agent" in seen["kwargs"]["headers"]

    def test_watch_page_uses_the_longer_legacy_timeout(self, monkeypatch):
        seen = []

        class Resp:
            text = ""

        def fake_http_get(url, **kwargs):
            seen.append((url, kwargs["timeout"]))
            return Resp()

        monkeypatch.setattr("rch.core.http.http_get", fake_http_get)

        get_video_info_fallback(_VIDEO_ID)

        assert seen == [(_OEMBED, 8), (_WATCH_PAGE, 15)]
