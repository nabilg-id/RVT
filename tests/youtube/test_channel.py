"""Tests for rch.youtube.channel — channel-scoped harvest.

Covers the pure helpers (URL normalisation, folder-name disambiguation,
duration/date filtering, JS-compatible int parsing) and the three orchestration
modes (``channel-info``, ``channel-video``, ``channel-full``) ported from
the Node.js ``lib/youtube/channelFull.js`` and ``channelVideo.js``.

Every external boundary (yt-dlp enumeration, metadata lookup, per-video
download, thumbnail HTTP fetch, sleep) is an injected fake — no network, no
subprocess, no clock.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from rch.core.checkpoint import load_checkpoint, save_checkpoint
from rch.core.events import create_emitter

# ``channel`` is already a fixture name in this file, so the module is reached
# through an alias when a test needs to patch something on it.
from rch.youtube import channel as channel_module
from rch.youtube.channel import (
    _RETRY_DELAY_SECONDS,
    _THUMBNAIL_RETRIES,
    _THUMBNAIL_TIMEOUT_SECONDS,
    _UNAVAILABLE_DESCRIPTION,
    MetadataClient,
    _default_download_image,
    _default_download_video,
    _default_sleep,
    _ZipCollector,
    base_folder_name,
    build_report,
    channel_full,
    channel_info,
    channel_slug,
    channel_video,
    count_slug_collisions,
    filter_ids,
    folder_name_for,
    list_ids,
    normalize_channel_url,
    parse_int,
    resolve_metadata,
    slug_or_none,
    thumbnail_url,
)

CHANNEL_URL = "https://www.youtube.com/@labrobotika1762"


# ---------------------------------------------------------------------------
# slug_or_none — JS-compatible slugify that can yield "no slug"
# ---------------------------------------------------------------------------


class TestSlugOrNone:
    def test_none_input_yields_none(self):
        assert slug_or_none(None) is None

    def test_symbol_only_input_yields_none(self):
        assert slug_or_none("!!! ???") is None

    def test_normal_title_yields_slug(self):
        assert slug_or_none("Halo Dunia") == "halo-dunia"

    def test_literal_video_title_is_not_confused_with_fallback(self):
        assert slug_or_none("video") == "video"

    def test_empty_string_yields_none(self):
        assert slug_or_none("") is None

    def test_non_string_is_coerced(self):
        assert slug_or_none(12345) == "12345"


# ---------------------------------------------------------------------------
# normalize_channel_url — channel id / handle / URL normalisation
# ---------------------------------------------------------------------------


class TestNormalizeChannelUrl:
    def test_full_url_passes_through(self):
        assert normalize_channel_url(CHANNEL_URL) == CHANNEL_URL

    def test_bare_channel_id_is_expanded(self):
        channel_id = "UC" + "a" * 22
        assert normalize_channel_url(channel_id) == (
            f"https://www.youtube.com/channel/{channel_id}"
        )

    def test_bare_handle_is_expanded(self):
        assert normalize_channel_url("@namachannel") == (
            "https://www.youtube.com/@namachannel"
        )

    def test_handle_url_is_left_alone(self):
        url = "https://www.youtube.com/@namachannel"
        assert normalize_channel_url(url) == url

    def test_whitespace_is_trimmed(self):
        assert normalize_channel_url(f"  {CHANNEL_URL}  ") == CHANNEL_URL

    def test_shorts_suffix_appended(self):
        assert normalize_channel_url(CHANNEL_URL, shorts=True) == (
            f"{CHANNEL_URL}/shorts"
        )

    def test_shorts_suffix_not_duplicated(self):
        shorts_url = f"{CHANNEL_URL}/shorts"
        assert normalize_channel_url(shorts_url, shorts=True) == shorts_url

    def test_shorts_suffix_strips_existing_trailing_slash(self):
        assert normalize_channel_url(f"{CHANNEL_URL}/", shorts=True) == (
            f"{CHANNEL_URL}/shorts"
        )

    def test_shorts_from_bare_handle(self):
        assert normalize_channel_url("@chan", shorts=True) == (
            "https://www.youtube.com/@chan/shorts"
        )

    def test_unrecognised_input_is_returned_verbatim(self):
        assert normalize_channel_url("UCtooshort") == "UCtooshort"


# ---------------------------------------------------------------------------
# channel_slug — output folder / zip name stem
# ---------------------------------------------------------------------------


class TestChannelSlug:
    def test_scheme_is_stripped_and_non_alnum_hyphenated(self):
        assert channel_slug(CHANNEL_URL) == "www-youtube-com-labrobotika1762"

    def test_bare_handle_slug(self):
        assert channel_slug("@namachannel") == "namachannel"

    def test_url_without_scheme(self):
        assert channel_slug("youtube.com/@chan") == "youtube-com-chan"

    def test_empty_input_falls_back_to_channel(self):
        assert channel_slug("") == "channel"

    def test_none_input_falls_back_to_channel(self):
        assert channel_slug(None) == "channel"

    def test_result_is_truncated_to_max_slug_length(self):
        slug = channel_slug("https://youtube.com/" + "a" * 300)
        assert len(slug) == 100

    def test_slug_is_lowercase(self):
        assert channel_slug("https://youtube.com/@MyChannel").endswith("mychannel")


# ---------------------------------------------------------------------------
# base_folder_name / slug collisions / folder_name_for
# ---------------------------------------------------------------------------


class TestFolderNaming:
    def test_title_becomes_slug(self):
        assert base_folder_name("ddd444", "Judul Biasa") == "judul-biasa"

    def test_symbol_only_title_falls_back_to_video_id(self):
        assert base_folder_name("ddd444", "###") == "ddd444"

    def test_missing_title_becomes_unavailable_prefixed(self):
        assert base_folder_name("xyz999", None) == "unavailable-xyz999"

    def test_empty_title_becomes_unavailable_prefixed(self):
        assert base_folder_name("xyz999", "") == "unavailable-xyz999"

    def test_collisions_counted_per_base_slug(self):
        ids = ["aaa111", "bbb222", "ccc333"]
        meta = {
            "aaa111": {"title": "Jasa pembuatan alat"},
            "bbb222": {"title": "Jasa pembuatan alat"},
            "ccc333": {"title": "Video unik"},
        }
        assert count_slug_collisions(ids, meta) == {
            "jasa-pembuatan-alat": 2,
            "video-unik": 1,
        }

    def test_collisions_count_missing_metadata_as_unavailable(self):
        ids = ["aaa111", "bbb222"]
        assert count_slug_collisions(ids, {}) == {
            "unavailable-aaa111": 1,
            "unavailable-bbb222": 1,
        }

    def test_duplicate_title_gets_id_suffix(self):
        counts = {"jasa-pembuatan-alat": 2}
        assert folder_name_for("aaa111", "Jasa pembuatan alat", counts) == (
            "jasa-pembuatan-alat-aaa111"
        )
        assert folder_name_for("bbb222", "Jasa pembuatan alat", counts) == (
            "jasa-pembuatan-alat-bbb222"
        )

    def test_unique_title_has_no_suffix(self):
        assert folder_name_for("ccc333", "Video unik", {"video-unik": 1}) == "video-unik"

    def test_unknown_base_slug_treated_as_unique(self):
        assert folder_name_for("zzz000", "Judul", {}) == "judul"

    def test_duplicate_unavailable_titles_stay_distinct(self):
        counts = {"unavailable-xyz999": 1}
        assert folder_name_for("xyz999", None, counts) == "unavailable-xyz999"

    def test_title_path_traversal_is_neutralised(self):
        name = folder_name_for("evil", "../../etc/passwd", {"etc-passwd": 1})
        assert "/" not in name
        assert ".." not in name


# ---------------------------------------------------------------------------
# parse_int — JavaScript parseInt-compatible coercion
# ---------------------------------------------------------------------------


class TestParseInt:
    def test_none_yields_none(self):
        assert parse_int(None) is None

    def test_empty_string_yields_none(self):
        assert parse_int("") is None

    def test_zero_is_none_because_falsy_guard(self):
        assert parse_int(0) is None

    def test_plain_number(self):
        assert parse_int("60") == 60

    def test_integer_passthrough(self):
        assert parse_int(120) == 120

    def test_trailing_garbage_ignored_like_js(self):
        assert parse_int("60abc") == 60

    def test_non_numeric_yields_none(self):
        assert parse_int("abc") is None

    def test_surrounding_whitespace_tolerated(self):
        assert parse_int("  42 ") == 42

    def test_signed_value(self):
        assert parse_int("-5") == -5

    def test_float_truncates_toward_zero(self):
        assert parse_int(60.9) == 60

    def test_bool_is_rejected(self):
        assert parse_int(True) is None
        assert parse_int(False) is None

    def test_unicode_digits_rejected_like_js(self):
        assert parse_int("１２") is None


# ---------------------------------------------------------------------------
# filter_ids — duration and upload-date filters
# ---------------------------------------------------------------------------


class TestFilterIds:
    IDS = ["a1", "b2", "c3", "d4"]
    META = {
        "a1": {"duration": 30, "uploadDate": "20240101"},
        "b2": {"duration": 120, "uploadDate": "20240601"},
        "c3": {"duration": None, "uploadDate": "20230101"},
        "d4": {"duration": 300, "uploadDate": "20241231"},
    }

    def test_no_filters_keeps_everything(self):
        assert filter_ids(self.IDS, self.META, {}) == self.IDS

    def test_min_duration_drops_short_videos(self):
        kept = filter_ids(self.IDS, self.META, {"minDuration": "60"})
        assert kept == ["b2", "c3", "d4"]

    def test_max_duration_drops_long_videos(self):
        kept = filter_ids(self.IDS, self.META, {"maxDuration": "200"})
        assert kept == ["a1", "b2", "c3"]

    def test_duration_range_combines_both_bounds(self):
        kept = filter_ids(self.IDS, self.META, {"minDuration": "60", "maxDuration": "200"})
        assert kept == ["b2", "c3"]

    def test_unknown_duration_survives_duration_filters(self):
        assert "c3" in filter_ids(self.IDS, self.META, {"minDuration": "1000"})

    def test_after_date_drops_older_uploads(self):
        kept = filter_ids(self.IDS, self.META, {"after": "2024-06-01"})
        assert kept == ["b2", "d4"]

    def test_after_accepts_compact_date_form(self):
        kept = filter_ids(self.IDS, self.META, {"after": "20240601"})
        assert kept == ["b2", "d4"]

    def test_after_keeps_same_day_upload(self):
        assert filter_ids(["b2"], self.META, {"after": "2024-06-01"}) == ["b2"]

    def test_missing_upload_date_survives_date_filter(self):
        assert filter_ids(["c3"], self.META, {"after": "20200101"}) == ["c3"]

    def test_missing_metadata_entry_survives_all_filters(self):
        assert filter_ids(["zz"], {}, {"minDuration": "10", "after": "20000101"}) == ["zz"]

    def test_non_numeric_bounds_are_ignored(self):
        assert filter_ids(self.IDS, self.META, {"minDuration": "abc"}) == self.IDS


class TestFilterDurationBoundaries:
    """Pin the inclusive semantics of the duration bounds.

    ``--min-duration`` and ``--max-duration`` are both inclusive: a video whose
    duration sits exactly on the bound satisfies it. That matches ``--after``,
    which also keeps a video uploaded on the boundary date (see
    ``TestFilterIds.test_after_keeps_same_day_upload``).

    The fixtures here deliberately place durations exactly *on* the bound.
    The pre-existing tests all use off-boundary durations (30/120/300 against
    bounds of 60/200), so they cannot distinguish ``<`` from ``<=``: swapping
    either comparison for an off-by-one leaves the whole suite green. These
    cases close that hole.
    """

    def test_min_duration_equal_to_bound_is_kept(self):
        meta = {"a1": {"duration": 60}, "b2": {"duration": 59}}
        assert filter_ids(["a1", "b2"], meta, {"minDuration": "60"}) == ["a1"]

    def test_max_duration_equal_to_bound_is_kept(self):
        meta = {"a1": {"duration": 200}, "b2": {"duration": 201}}
        assert filter_ids(["a1", "b2"], meta, {"maxDuration": "200"}) == ["a1"]

    def test_exact_single_value_range_keeps_only_the_match(self):
        meta = {
            "a1": {"duration": 100},
            "b2": {"duration": 99},
            "c3": {"duration": 101},
        }
        kept = filter_ids(
            ["a1", "b2", "c3"], meta,
            {"minDuration": "100", "maxDuration": "100"},
        )
        assert kept == ["a1"]

    def test_min_duration_zero_is_treated_as_no_filter(self):
        # ``parse_int("0")`` returns 0, which is falsy, so the guard inside
        # ``filter_ids`` skips the comparison entirely. Pinned deliberately:
        # a zero bound means "no bound", not "nothing passes".
        meta = {"a1": {"duration": 0}, "b2": {"duration": 30}}
        assert filter_ids(["a1", "b2"], meta, {"minDuration": "0"}) == ["a1", "b2"]

    def test_max_duration_zero_is_treated_as_no_filter(self):
        meta = {"a1": {"duration": 0}, "b2": {"duration": 30}}
        assert filter_ids(["a1", "b2"], meta, {"maxDuration": "0"}) == ["a1", "b2"]


# ---------------------------------------------------------------------------
# thumbnail_url
# ---------------------------------------------------------------------------


class TestThumbnailUrl:
    def test_default_size_is_hqdefault(self):
        assert thumbnail_url("dQw4w9WgXcQ") == (
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        )

    def test_custom_size(self):
        assert thumbnail_url("abc", "maxresdefault") == (
            "https://i.ytimg.com/vi/abc/maxresdefault.jpg"
        )


# ---------------------------------------------------------------------------
# Shared fakes for the orchestration modes
# ---------------------------------------------------------------------------


class _FakeIds:
    """Fake ``get_channel_ids`` recording the URL it was asked to enumerate."""

    def __init__(self, ids=None, error=None):
        self._ids = list(ids or [])
        self._error = error
        self.calls = []

    def __call__(self, source_url):
        self.calls.append(source_url)
        if self._error is not None:
            raise self._error
        return list(self._ids)


class _FakeMeta:
    """Fake metadata bundle: batch, per-video, and oEmbed-style fallback."""

    def __init__(self, batch=None, batch_error=None, single=None, fallback=None):
        self._batch = batch if batch is not None else {}
        self._batch_error = batch_error
        self._single = single or {}
        self._fallback = fallback or {}
        self.single_calls = []
        self.fallback_calls = []

    def batch(self, ids):
        if self._batch_error is not None:
            raise self._batch_error
        return dict(self._batch)

    def single(self, url):
        self.single_calls.append(url)
        raise self._single.get(url, KeyError(url))

    def fallback(self, video_id):
        self.fallback_calls.append(video_id)
        return self._fallback.get(video_id)


class _FakeDownload:
    """Fake ``video.download`` writing a real file so zip/paths behave."""

    def __init__(self, failing=None, erroring=None):
        self._failing = set(failing or [])
        self._erroring = set(erroring or [])
        self.calls = []

    def __call__(self, url, options=None):
        self.calls.append((url, dict(options or {})))
        video_id = url.rsplit("/", 1)[-1]
        if video_id in self._erroring:
            raise RuntimeError(f"boom-{video_id}")
        if video_id in self._failing:
            return {"status": False, "message": f"gagal-{video_id}"}
        out_dir = Path(options["outputDir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{options.get('filename', 'video')}.mp4"
        path.write_bytes(b"v" * 8)
        return {"status": True, "result": {"id": video_id, "path": str(path)}}


class _FakeSleep:
    """Records sleep durations instead of waiting."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _video_ids(*ids):
    return list(ids)


def _unexpected(*_args, **_kwargs):
    raise AssertionError("this metadata boundary should not be called")


def _video_opts(tmp_path, **overrides):
    opts = {
        "outputDir": str(tmp_path / "downloads"),
        "concurrency": 1,
    }
    opts.update(overrides)
    return opts


# ---------------------------------------------------------------------------
# channel_video — video-only harvest
# ---------------------------------------------------------------------------


class TestChannelVideoHappyPath:
    def test_returns_success_envelope_with_totals(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001", "vid00000002")),
            metadata=_FakeMeta(batch={
                "vid00000001": {"title": "Video Satu", "duration": 60,
                                "uploadDate": "20240101", "description": "d1"},
                "vid00000002": {"title": "Video Dua", "duration": 90,
                                "uploadDate": "20240201", "description": "d2"},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is True
        body = result["result"]
        assert body["total"] == 2
        assert body["success"] == 2
        assert body["failed"] == 0
        assert body["channel"] == CHANNEL_URL

    def test_items_carry_video_id_title_and_metadata(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={
                "vid00000001": {"title": "Video Satu", "duration": 60,
                                "uploadDate": "20240101", "description": "d1"},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["id"] == "vid00000001"
        assert item["videoId"] == "vid00000001"
        assert item["title"] == "Video Satu"
        assert item["duration"] == 60
        assert item["uploadDate"] == "20240101"
        assert item["description"] == "d1"
        assert item["url"] == "https://youtu.be/vid00000001"
        assert item["ok"] is True
        assert item["skipped"] is False
        assert item["error"] is None

    def test_downloads_into_slugified_folder(self, tmp_path):
        downloader = _FakeDownload()
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "Judul Baru!"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert result["status"] is True
        _, opts = downloader.calls[0]
        assert Path(opts["outputDir"]).name == "judul-baru"
        assert Path(result["result"]["workDir"]).is_dir()

    def test_zip_name_defaults_to_channel_videos_zip(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        zip_path = Path(result["result"]["zipPath"])
        assert zip_path.name == "www-youtube-com-labrobotika1762-videos.zip"
        assert zip_path.exists()

    def test_zip_contains_video_entry_per_item(self, tmp_path):
        import zipfile

        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            assert "satu/video.mp4" in zf.namelist()

    def test_custom_zip_name_is_honoured(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, zipName="custom.zip"),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["zipPath"]).name == "custom.zip"

    def test_quality_and_subtitle_options_forwarded_to_download(self, tmp_path):
        downloader = _FakeDownload()
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, quality="1080p", subtitles=True, subLang="en"),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        _, opts = downloader.calls[0]
        assert opts["format"] == "mp4"
        assert opts["quality"] == "1080p"
        assert opts["subtitles"] is True
        assert opts["subLang"] == "en"
        assert opts["filename"] == "video"

    def test_default_quality_is_720p_and_sublang_all(self, tmp_path):
        downloader = _FakeDownload()
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001")),
            metadata=_FakeMeta(batch={"vid00000001": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        _, opts = downloader.calls[0]
        assert opts["quality"] == "720p"
        assert opts["subtitles"] is False
        assert opts["subLang"] == "all"

    def test_duplicate_titles_get_distinct_folders(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("vid00000001", "vid00000002")),
            metadata=_FakeMeta(batch={
                "vid00000001": {"title": "Sama"},
                "vid00000002": {"title": "Sama"},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        work_dir = Path(result["result"]["workDir"])
        assert (work_dir / "sama-vid00000001").is_dir()
        assert (work_dir / "sama-vid00000002").is_dir()

    def test_concurrency_greater_than_one_processes_everything(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, concurrency=4),
            list_ids=_FakeIds(_video_ids("a1", "b2", "c3", "d4")),
            metadata=_FakeMeta(batch={v: {"title": f"T{i}"} for i, v in
                                      enumerate(["a1", "b2", "c3", "d4"])}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["total"] == 4
        assert result["result"]["success"] == 4


class TestChannelVideoEnumerationFailures:
    def test_enumeration_error_returns_failure_envelope(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(error=RuntimeError("yt-dlp tidak ditemukan")),
            metadata=_FakeMeta(),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is False
        assert "Gagal ambil daftar video" in result["message"]
        assert "yt-dlp tidak ditemukan" in result["message"]

    def test_empty_channel_returns_failure(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds([]),
            metadata=_FakeMeta(),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is False
        assert result["message"] == "Channel tidak memiliki video."

    def test_limit_truncates_enumerated_ids(self, tmp_path):
        downloader = _FakeDownload()
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, limit="1"),
            list_ids=_FakeIds(_video_ids("a1", "b2", "c3")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert result["result"]["total"] == 1
        assert len(downloader.calls) == 1

    def test_shorts_mode_enumerates_shorts_tab(self, tmp_path):
        lister = _FakeIds(_video_ids("a1"))
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, shorts=True),
            list_ids=lister,
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert lister.calls == [f"{CHANNEL_URL}/shorts"]

    def test_shorts_mode_uses_shorts_suffixed_names(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, shorts=True),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["zipPath"]).name.endswith("-shorts-videos.zip")
        assert Path(result["result"]["workDir"]).name.endswith("-shorts-videos")


class TestChannelVideoMetadataFallback:
    def test_batch_metadata_gap_triggers_single_lookup(self, tmp_path):
        meta = _FakeMeta(batch={"a1": {"title": "A"}}, fallback={"b2": {"title": "B Single"}})
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=meta,
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert meta.single_calls == ["https://youtu.be/b2"]
        assert meta.fallback_calls == ["b2"]

    def test_failed_batch_falls_back_to_single_lookups(self, tmp_path):
        meta = _FakeMeta(
            batch_error=RuntimeError("batch blew up"),
            fallback={"a1": {"title": "A Single"}, "b2": {"title": "B Single"}},
        )
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=meta,
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        titles = sorted(i["title"] for i in result["result"]["items"])
        assert titles == ["A Single", "B Single"]

    def test_unresolvable_metadata_becomes_placeholder_entry(self, tmp_path):
        meta = _FakeMeta(batch={})
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=meta,
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["title"] is None
        assert item["url"] == "https://youtu.be/a1"

    def test_missing_metadata_folder_is_unavailable_prefixed(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["workDir"], "unavailable-a1").is_dir()


class TestChannelVideoFilters:
    def test_duration_filter_reduces_total(self, tmp_path):
        downloader = _FakeDownload()
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, minDuration="100"),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "Pendek", "duration": 30},
                "b2": {"title": "Panjang", "duration": 300},
            }),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert result["result"]["total"] == 1
        assert len(downloader.calls) == 1

    def test_filter_matching_nothing_returns_failure(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, maxDuration="10"),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Panjang", "duration": 900}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is False
        assert result["message"] == "Tidak ada video yang cocok dengan filter."

    def test_after_filter_drops_old_uploads(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, after="2024-06-01"),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "Lama", "uploadDate": "20230101"},
                "b2": {"title": "Baru", "uploadDate": "20240701"},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert [i["videoId"] for i in result["result"]["items"]] == ["b2"]


class TestChannelVideoUnavailableAndRetry:
    def test_failed_download_recorded_not_fatal(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"}}),
            download_video=_FakeDownload(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        assert result["status"] is True
        body = result["result"]
        assert body["success"] == 1
        assert body["failed"] == 1
        failed_item = [i for i in body["items"] if i["videoId"] == "a1"][0]
        assert failed_item["ok"] is False
        assert "gagal-a1" in failed_item["error"]

    def test_download_exception_recorded_as_error(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(erroring=["a1"]),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["ok"] is False
        assert "boom-a1" in item["error"]

    def test_failed_items_are_retried_once_by_default(self, tmp_path):
        downloader = _FakeDownload(failing=["a1"])
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert len(downloader.calls) == 2
        assert result["result"]["failed"] == 1

    def test_retry_recovery_flips_item_to_success(self, tmp_path):
        class _Flaky:
            def __init__(self):
                self.calls = []

            def __call__(self, url, options=None):
                self.calls.append(url)
                if len(self.calls) == 1:
                    return {"status": False, "message": "HTTP Error 403"}
                out_dir = Path(options["outputDir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / "video.mp4"
                path.write_bytes(b"x" * 4)
                return {"status": True, "result": {"path": str(path)}}

        downloader = _Flaky()
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert result["result"]["success"] == 1
        assert result["result"]["failed"] == 0
        assert result["result"]["items"][0]["error"] is None

    def test_retry_sleeps_between_attempts(self, tmp_path):
        sleeper = _FakeSleep()
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(failing=["a1"]),
            sleep=sleeper,
        )
        assert sleeper.calls == [_RETRY_DELAY_SECONDS]

    def test_no_sleep_when_nothing_failed(self, tmp_path):
        sleeper = _FakeSleep()
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=sleeper,
        )
        assert sleeper.calls == []

    def test_retry_disabled_skips_second_attempt(self, tmp_path):
        downloader = _FakeDownload(failing=["a1"])
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert len(downloader.calls) == 1

    def test_skipped_items_are_not_retried(self, tmp_path):
        downloader = _FakeDownload()
        opts = _video_opts(tmp_path, resume=True, retryFailed=True)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 1,
            "completed": ["a1"], "failed": [],
        })
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []


class TestChannelVideoEvents:
    def test_progress_events_reported_for_each_video(self, tmp_path):
        seen = []
        emitter = create_emitter()
        emitter.on("progress", lambda p: seen.append(p))
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
            emitter=emitter,
        )
        assert [p["done"] for p in seen] == [1, 2]
        assert all(p["total"] == 2 for p in seen)
        assert {p["label"] for p in seen} == {"A", "B"}

    def test_lifecycle_events_emitted_in_order(self, tmp_path):
        seen = []
        emitter = create_emitter()
        for name in ("phase", "start", "video:start", "video:done", "complete"):
            emitter.on(name, lambda p, _n=name: seen.append(_n))
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
            emitter=emitter,
        )
        assert seen == ["phase", "start", "video:start", "video:done", "complete"]

    def test_on_progress_callback_receives_counts(self, tmp_path):
        calls = []
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, onProgress=lambda done, total, label: calls.append((done, total, label))),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert calls == [(1, 1, "A")]

    def test_on_phase_callback_reports_phases(self, tmp_path):
        phases = []
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, onPhase=lambda n, m: phases.append(n)),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert phases == ["list", "meta", "download"]

    def test_retry_phase_reported_when_items_fail(self, tmp_path):
        phases = []
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, onPhase=lambda n, m: phases.append(n)),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        assert "retry" in phases

    def test_filter_phase_reported_when_filter_drops_items(self, tmp_path):
        phases = []
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, minDuration="100",
                        onPhase=lambda n, m: phases.append(n)),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "A", "duration": 10},
                "b2": {"title": "B", "duration": 200},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert "filter" in phases

    def test_works_without_any_hooks_supplied(self, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            {"outputDir": str(tmp_path / "d")},
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is True


class TestChannelVideoCheckpointResume:
    def test_all_success_clears_checkpoint(self, tmp_path):
        opts = _video_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 1,
            "completed": ["zz"], "failed": [],
        })
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert load_checkpoint(str(output_dir)) is None

    def test_partial_failure_keeps_checkpoint_for_next_run(self, tmp_path):
        opts = _video_opts(tmp_path, retryFailed=False)
        output_dir = Path(opts["outputDir"])
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"}}),
            download_video=_FakeDownload(failing=["b2"]),
            sleep=_FakeSleep(),
        )
        cp = load_checkpoint(str(output_dir))
        assert cp is not None
        assert cp["completed"] == ["a1"]
        assert cp["failed"] == []

    def test_resume_restore_records_channel_and_total(self, tmp_path):
        opts = _video_opts(tmp_path, resume=True, retryFailed=False)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 2,
            "completed": ["a1"], "failed": [],
        })
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"}}),
            download_video=_FakeDownload(failing=["b2"]),
            sleep=_FakeSleep(),
        )
        cp = load_checkpoint(str(output_dir))
        assert cp["channel"] == CHANNEL_URL
        assert cp["total"] == 2
        assert cp["completed"] == ["a1"]

    def test_all_failure_run_without_prior_state_leaves_no_checkpoint(self, tmp_path):
        opts = _video_opts(tmp_path, retryFailed=False)
        output_dir = Path(opts["outputDir"])
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        assert load_checkpoint(str(output_dir)) is None

    def test_completed_ids_written_to_checkpoint(self, tmp_path):
        opts = _video_opts(tmp_path)
        output_dir = Path(opts["outputDir"])
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert output_dir.joinpath(".rch-checkpoint.json").exists() is False

    def test_existing_video_file_skipped_on_resume(self, tmp_path):
        opts = _video_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        work_dir = output_dir / "www-youtube-com-labrobotika1762-videos"
        folder = work_dir / "a"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        downloader = _FakeDownload()
        result = channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []
        assert result["result"]["items"][0]["skipped"] is True

    def test_resumed_file_is_still_added_to_zip(self, tmp_path):
        import zipfile

        opts = _video_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        folder = output_dir / "www-youtube-com-labrobotika1762-videos" / "a"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        result = channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            assert "a/video.mp4" in zf.namelist()

    def test_resume_disabled_redownloads_even_when_file_present(self, tmp_path):
        opts = _video_opts(tmp_path, resume=False)
        output_dir = Path(opts["outputDir"])
        folder = output_dir / "www-youtube-com-labrobotika1762-videos" / "a"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        downloader = _FakeDownload()
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert len(downloader.calls) == 1

    def test_checkpoint_restored_with_previous_completions(self, tmp_path):
        opts = _video_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 2,
            "completed": ["a1"], "failed": [],
        })
        downloader = _FakeDownload()
        result = channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"}}),
            download_video=downloader,
            sleep=_FakeSleep(),
        )
        assert [url for url, _opts in downloader.calls] == ["https://youtu.be/b2"]
        assert result["result"]["success"] == 2

    def test_checkpoint_failed_list_preserved_on_restore(self, tmp_path):
        opts = _video_opts(tmp_path, resume=True, retryFailed=False)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 1,
            "completed": [], "failed": ["old-fail"],
        })
        channel_video(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        assert "old-fail" in load_checkpoint(str(output_dir))["failed"]


# ---------------------------------------------------------------------------
# channel_full — video + thumbnail + description + link + report + zip
# ---------------------------------------------------------------------------


class _FakeImage:
    """Fake thumbnail HTTP fetch returning image bytes."""

    def __init__(self, failing=None):
        self._failing = set(failing or [])
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if any(vid in url for vid in self._failing):
            raise RuntimeError(f"404-{url}")
        return b"IMGDATA" * 4


def _full_opts(tmp_path, **overrides):
    opts = {"outputDir": str(tmp_path / "downloads"), "concurrency": 1}
    opts.update(overrides)
    return opts


class TestChannelFullHappyPath:
    def test_returns_success_envelope_with_counts(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}, "b2": {"title": "Dua"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        body = result["result"]
        assert result["status"] is True
        assert body["total"] == 2
        assert body["success"] == 2
        assert body["failed"] == 0
        assert body["unavailable"] == 0
        assert body["unavailableIds"] == []

    def test_item_shape_matches_legacy(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu", "duration": 90,
                                            "uploadDate": "20240505",
                                            "description": "hello"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert set(item) == {
            "id", "videoId", "title", "duration", "uploadDate", "description",
            "url", "videoOk", "thumbOk", "unavailable", "error",
        }
        assert item["videoOk"] is True
        assert item["thumbOk"] is True
        assert item["unavailable"] is False
        assert item["error"] is None
        assert item["description"] == "hello"

    def test_writes_description_and_link_files(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu", "description": "isi"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        folder = Path(result["result"]["workDir"], "satu")
        meta = json.loads(folder.joinpath("metadata.json").read_text(encoding="utf-8"))
        assert meta["description"] == "isi"
        assert meta["url"] == "https://youtu.be/a1"
        assert folder.joinpath("thumbnail.jpg").exists()

    def test_empty_description_stays_empty_and_is_not_unavailable(self, tmp_path):
        """A real video with no description is not the same as a missing one."""
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        folder = Path(result["result"]["workDir"], "satu")
        meta = json.loads(folder.joinpath("metadata.json").read_text(encoding="utf-8"))
        assert meta["description"] == ""
        assert meta["unavailable"] is False

    def test_zip_contains_video_thumbnail_and_metadata(self, tmp_path):
        import zipfile

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = set(zf.namelist())
        assert names == {"satu/video.mp4", "satu/thumbnail.jpg",
                         "satu/metadata.json"}

    def test_zip_name_defaults_to_full_suffix(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["zipPath"]).name == \
            "www-youtube-com-labrobotika1762-full.zip"
        assert Path(result["result"]["workDir"]).name == \
            "www-youtube-com-labrobotika1762-full"

    def test_custom_zip_name_honoured(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, zipName="my.zip"),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["zipPath"]).name == "my.zip"

    def test_shorts_mode_names(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, shorts=True),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["zipPath"]).name.endswith("-shorts-full.zip")
        assert Path(result["result"]["workDir"]).name.endswith("-shorts-full")

    def test_thumbnail_size_forwarded(self, tmp_path):
        images = _FakeImage()
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, size="maxresdefault"),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=images,
            sleep=_FakeSleep(),
        )
        assert images.calls == ["https://i.ytimg.com/vi/a1/maxresdefault.jpg"]

    def test_default_thumbnail_size_is_hqdefault(self, tmp_path):
        images = _FakeImage()
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=images and _FakeDownload(),
            download_image=images,
            sleep=_FakeSleep(),
        )
        assert images.calls == ["https://i.ytimg.com/vi/a1/hqdefault.jpg"]

    def test_duplicate_titles_disambiguated(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "Sama"},
                                      "b2": {"title": "Sama"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        work_dir = Path(result["result"]["workDir"])
        assert (work_dir / "sama-a1" / "metadata.json").exists()
        assert (work_dir / "sama-b2" / "metadata.json").exists()

    def test_multiple_concurrency_processes_all(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, concurrency=3),
            list_ids=_FakeIds(_video_ids("a1", "b2", "c3")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}, "b2": {"title": "B"},
                                      "c3": {"title": "C"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["success"] == 3


class TestChannelFullUnavailable:
    def test_unavailable_video_skips_download_but_records(self, tmp_path):
        downloader = _FakeDownload()
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1", "gone")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}, "gone": {"title": None}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert [url for url, _o in downloader.calls] == ["https://youtu.be/a1"]
        body = result["result"]
        assert body["unavailable"] == 1
        assert body["unavailableIds"] == ["gone"]
        assert body["success"] == 1
        assert body["failed"] == 1

    def test_unavailable_item_shape_and_flags(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("gone")),
            metadata=_FakeMeta(batch={"gone": {"title": None}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["unavailable"] is True
        assert item["videoOk"] is False
        assert item["thumbOk"] is True
        assert item["error"] is None

    def test_unavailable_folder_named_with_prefix(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("gone")),
            metadata=_FakeMeta(batch={"gone": {"title": None}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        folder = Path(result["result"]["workDir"], "unavailable-gone")
        meta = json.loads(folder.joinpath("metadata.json").read_text(encoding="utf-8"))
        assert meta["description"] == _UNAVAILABLE_DESCRIPTION
        assert meta["unavailable"] is True

    def test_unavailable_zip_excludes_video_entry(self, tmp_path):
        import zipfile

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("gone")),
            metadata=_FakeMeta(batch={"gone": {"title": None}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = set(zf.namelist())
        assert "unavailable-gone/video.mp4" not in names
        assert "unavailable-gone/thumbnail.jpg" in names
        assert "unavailable-gone/metadata.json" in names

    def test_unavailable_videos_not_retried(self, tmp_path):
        downloader = _FakeDownload()
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("gone")),
            metadata=_FakeMeta(batch={"gone": {"title": None}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []


class TestChannelFullThumbnailAndVideoFailure:
    def test_thumbnail_failure_recorded_but_not_fatal(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["thumbOk"] is False
        assert item["videoOk"] is True
        assert result["result"]["success"] == 1

    def test_thumbnail_failure_omits_zip_entry(self, tmp_path):
        import zipfile

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(failing=["a1"]),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = set(zf.namelist())
        assert "satu/thumbnail.jpg" not in names
        assert "satu/metadata.json" in names

    def test_video_failure_recorded_with_error(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(failing=["a1"]),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        item = result["result"]["items"][0]
        assert item["videoOk"] is False
        assert "gagal-a1" in item["error"]
        assert result["result"]["failed"] == 1

    def test_video_failure_omits_video_zip_entry(self, tmp_path):
        import zipfile

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(failing=["a1"]),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            assert "satu/video.mp4" not in zf.namelist()

    def test_failed_videos_retried_once(self, tmp_path):
        downloader = _FakeDownload(failing=["a1"])
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert len(downloader.calls) == 2

    def test_retry_recovery_flips_video_ok(self, tmp_path):
        class _Flaky:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, options=None):
                self.calls += 1
                if self.calls == 1:
                    return {"status": False, "message": "HTTP Error 403"}
                out_dir = Path(options["outputDir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / "video.mp4"
                path.write_bytes(b"z" * 3)
                return {"status": True, "result": {"path": str(path)}}

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_Flaky(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["success"] == 1
        assert result["result"]["items"][0]["error"] is None

    def test_retry_sleeps(self, tmp_path):
        sleeper = _FakeSleep()
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(failing=["a1"]),
            download_image=_FakeImage(),
            sleep=sleeper,
        )
        assert sleeper.calls == [_RETRY_DELAY_SECONDS]

    def test_retry_disabled(self, tmp_path):
        downloader = _FakeDownload(failing=["a1"])
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert len(downloader.calls) == 1

    def test_include_video_false_skips_retry(self, tmp_path):
        downloader = _FakeDownload(failing=["a1"])
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, includeVideo=False, retryFailed=True),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []

    def test_processing_exception_produces_minimal_item(self, tmp_path):
        opts = _full_opts(tmp_path, retryFailed=False)
        blocker = Path(opts["outputDir"]) / "www-youtube-com-labrobotika1762-full" / "satu"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("not a directory", encoding="utf-8")

        result = channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is True
        item = result["result"]["items"][0]
        assert item["videoId"] == "a1"
        assert item["videoOk"] is False
        assert item["thumbOk"] is False
        assert item["error"]
        assert result["result"]["failed"] == 1


class TestChannelFullEnumerationAndFilters:
    def test_enumeration_error_returns_failure(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(error=RuntimeError("no yt-dlp")),
            metadata=_FakeMeta(),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is False
        assert result["message"] == "Gagal ambil daftar video: no yt-dlp"

    def test_empty_channel_returns_failure(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds([]),
            metadata=_FakeMeta(),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["message"] == "Channel tidak memiliki video."

    def test_limit_applies_before_metadata(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, limit="2"),
            list_ids=_FakeIds(_video_ids("a1", "b2", "c3")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["total"] == 2

    def test_filter_removes_items(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, minDuration="100"),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "Pendek", "duration": 10},
                "b2": {"title": "Panjang", "duration": 500},
            }),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert [i["videoId"] for i in result["result"]["items"]] == ["b2"]

    def test_filter_matching_nothing_returns_failure(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, after="2030-01-01"),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A", "uploadDate": "20200101"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["message"] == "Tidak ada video yang cocok dengan filter."

    def test_meta_fallback_phase_reported(self, tmp_path):
        phases = []
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, onPhase=lambda n, m: phases.append(n)),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={}, fallback={"a1": {"title": "A"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert "meta-fallback" in phases

    def test_placeholder_metadata_for_unresolvable_video(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["unavailableIds"] == ["a1"]


class TestChannelFullResumeAndEvents:
    def test_existing_video_skipped_on_resume(self, tmp_path):
        opts = _full_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        folder = output_dir / "www-youtube-com-labrobotika1762-full" / "satu"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        downloader = _FakeDownload()
        images = _FakeImage()
        result = channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=images,
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []
        assert images.calls == []
        assert result["result"]["items"][0]["videoOk"] is True

    def test_resume_adds_existing_video_to_zip(self, tmp_path):
        import zipfile

        opts = _full_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        folder = output_dir / "www-youtube-com-labrobotika1762-full" / "satu"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        result = channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            assert "satu/video.mp4" in zf.namelist()

    def test_include_video_false_never_skips_on_resume(self, tmp_path):
        opts = _full_opts(tmp_path, resume=True, includeVideo=False)
        output_dir = Path(opts["outputDir"])
        folder = output_dir / "www-youtube-com-labrobotika1762-full" / "satu"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"pre")

        images = _FakeImage()
        channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=images,
            sleep=_FakeSleep(),
        )
        assert images.calls == ["https://i.ytimg.com/vi/a1/hqdefault.jpg"]

    def test_filter_active_but_nothing_dropped_skips_phase(self, tmp_path):
        phases = []
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path, minDuration="10",
                        onPhase=lambda n, m: phases.append(n)),
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "A", "duration": 100},
                "b2": {"title": "B", "duration": 200},
            }),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["total"] == 2
        assert "filter" not in phases

    def test_resume_by_checkpoint_without_file_on_disk(self, tmp_path):
        opts = _full_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {
            "channel": CHANNEL_URL, "total": 1,
            "completed": ["a1"], "failed": [],
        })
        downloader = _FakeDownload()
        result = channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []
        assert result["result"]["items"][0]["videoOk"] is True

    def test_full_failure_keeps_checkpoint_for_next_run(self, tmp_path):
        opts = _full_opts(tmp_path, resume=True)
        output_dir = Path(opts["outputDir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        save_checkpoint(str(output_dir), {"channel": CHANNEL_URL, "total": 1,
                                          "completed": ["zz"], "failed": []})
        channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert load_checkpoint(str(output_dir)) is None

    def test_partial_failure_keeps_checkpoint(self, tmp_path):
        opts = _full_opts(tmp_path, retryFailed=False)
        output_dir = Path(opts["outputDir"])
        channel_full(
            CHANNEL_URL,
            opts,
            list_ids=_FakeIds(_video_ids("a1", "b2")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"},
                                      "b2": {"title": "Dua"}}),
            download_video=_FakeDownload(failing=["b2"]),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert load_checkpoint(str(output_dir))["completed"] == ["a1"]

    def test_lifecycle_events_emitted(self, tmp_path):
        seen = []
        emitter = create_emitter()
        for name in ("phase", "start", "video:start", "video:done", "progress",
                     "complete"):
            emitter.on(name, lambda p, _n=name: seen.append(_n))
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
            emitter=emitter,
        )
        assert seen == ["phase", "start", "video:start", "video:done",
                        "progress", "complete"]

    def test_progress_event_payload(self, tmp_path):
        seen = []
        emitter = create_emitter()
        emitter.on("progress", lambda p: seen.append(p))
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
            emitter=emitter,
        )
        assert seen == [{"done": 1, "total": 1, "label": "Satu"}]

    def test_progress_label_falls_back_to_video_id(self, tmp_path):
        seen = []
        emitter = create_emitter()
        emitter.on("progress", lambda p: seen.append(p))
        channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, retryFailed=False),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
            emitter=emitter,
        )
        assert seen[0]["label"] == "a1"


class TestChannelInfo:
    def test_info_mode_skips_video_download(self, tmp_path):
        downloader = _FakeDownload()
        images = _FakeImage()
        result = channel_info(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu", "description": "d"}}),
            download_video=downloader,
            download_image=images,
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []
        assert images.calls == ["https://i.ytimg.com/vi/a1/hqdefault.jpg"]
        assert result["result"]["success"] == 1

    def test_info_mode_zip_omits_video_entry(self, tmp_path):
        import zipfile

        result = channel_info(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = set(zf.namelist())
        assert names == {"satu/thumbnail.jpg", "satu/metadata.json"}

    def test_info_mode_unavailable_counted(self, tmp_path):
        result = channel_info(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "gone")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"},
                                      "gone": {"title": None}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["unavailable"] == 1
        assert result["result"]["unavailableIds"] == ["gone"]
        assert result["result"]["success"] == 1

    def test_info_mode_explicit_include_video_overridden(self, tmp_path):
        downloader = _FakeDownload()
        channel_info(
            CHANNEL_URL,
            _full_opts(tmp_path, includeVideo=True),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=downloader,
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert downloader.calls == []

    def test_info_mode_uses_full_folder_naming(self, tmp_path):
        result = channel_info(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert Path(result["result"]["workDir"]).name == \
            "www-youtube-com-labrobotika1762-full"


# ---------------------------------------------------------------------------
# Module-level list_ids — the public enumeration entry point used by `rch list`
# ---------------------------------------------------------------------------


class TestModuleLevelListIds:
    def test_exists_and_enumerates_via_metadata_client(self, stub_metadata):
        assert list_ids(CHANNEL_URL) == ["m1", "m2"]

    def test_returns_a_list_not_a_generator(self, stub_metadata):
        assert isinstance(list_ids(CHANNEL_URL), list)

    def test_normalises_a_bare_handle(self, stub_metadata):
        seen = {}
        module = sys.modules["rch.youtube.metadata"]
        module.get_channel_ids = lambda url: seen.setdefault("url", url) or ["a"]

        list_ids("@labrobotika1762")

        assert seen["url"] == "https://www.youtube.com/@labrobotika1762"

    def test_limit_truncates_the_result(self, stub_metadata):
        assert list_ids(CHANNEL_URL, {"limit": 1}) == ["m1"]

    def test_limit_zero_is_ignored_like_channel_video(self, stub_metadata):
        assert list_ids(CHANNEL_URL, {"limit": 0}) == ["m1", "m2"]

    def test_absent_limit_keeps_everything(self, stub_metadata):
        assert list_ids(CHANNEL_URL) == ["m1", "m2"]

    def test_non_numeric_limit_is_ignored(self, stub_metadata):
        assert list_ids(CHANNEL_URL, {"limit": "abc"}) == ["m1", "m2"]

    def test_options_may_be_none(self, stub_metadata):
        assert list_ids(CHANNEL_URL, None) == ["m1", "m2"]

    def test_shorts_flag_selects_the_shorts_tab(self, stub_metadata):
        seen = {}
        module = sys.modules["rch.youtube.metadata"]
        module.get_channel_ids = lambda url: seen.setdefault("url", url) or ["a"]

        list_ids(CHANNEL_URL, {"shorts": True})

        assert seen["url"].endswith("/shorts")

    def test_empty_channel_yields_empty_list(self, stub_metadata):
        sys.modules["rch.youtube.metadata"].get_channel_ids = lambda url: []

        assert list_ids(CHANNEL_URL) == []

    def test_enumeration_error_propagates(self, stub_metadata):
        def _boom(_url):
            raise RuntimeError("gagal")

        sys.modules["rch.youtube.metadata"].get_channel_ids = _boom

        with pytest.raises(RuntimeError, match="gagal"):
            list_ids(CHANNEL_URL)


# MetadataClient — default resolution against the metadata module
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_metadata(monkeypatch):
    """Install a stub ``rch.youtube.metadata`` so default imports resolve.

    Works whether or not the real metadata module exists yet, and keeps these
    tests free of network and subprocess access.
    """
    module = types.ModuleType("rch.youtube.metadata")
    module.get_channel_ids = lambda url: ["m1", "m2"]
    module.get_video_info_batch = lambda ids: {
        vid: {"id": vid, "title": f"T-{vid}"} for vid in ids
    }
    module.get_video_info = lambda url: {"id": url.rsplit("/", 1)[-1],
                                         "title": "single"}
    module.get_video_info_fallback = lambda vid: {"id": vid, "title": "fallback"}
    monkeypatch.setitem(sys.modules, "rch.youtube.metadata", module)
    monkeypatch.setattr(
        "rch.youtube.channel.MetadataClient", MetadataClient, raising=True
    )
    return module


class TestMetadataClientDefaults:
    def test_list_ids_uses_injected_callable(self):
        client = MetadataClient(get_channel_ids=lambda url: ["x", "y"])
        assert client.list_ids("u") == ["x", "y"]

    def test_batch_uses_injected_callable(self):
        client = MetadataClient(get_video_info_batch=lambda ids: {"a": {"id": "a"}})
        assert client.batch(["a"]) == {"a": {"id": "a"}}

    def test_single_uses_injected_callable(self):
        client = MetadataClient(get_video_info=lambda url: {"id": "z"})
        assert client.single("u") == {"id": "z"}

    def test_fallback_uses_injected_callable(self):
        client = MetadataClient(get_video_info_fallback=lambda vid: {"id": vid})
        assert client.fallback("q") == {"id": "q"}

    def test_fallback_may_return_none(self):
        client = MetadataClient(get_video_info_fallback=lambda _vid: None)
        assert client.fallback("q") is None

    def test_list_ids_falls_back_to_metadata_module(self, stub_metadata):
        assert MetadataClient().list_ids("https://x") == ["m1", "m2"]

    def test_batch_falls_back_to_metadata_module(self, stub_metadata):
        assert MetadataClient().batch(["a1"]) == {"a1": {"id": "a1", "title": "T-a1"}}

    def test_single_falls_back_to_metadata_module(self, stub_metadata):
        assert MetadataClient().single("https://youtu.be/z")["title"] == "single"

    def test_fallback_falls_back_to_metadata_module(self, stub_metadata):
        assert MetadataClient().fallback("z")["title"] == "fallback"

    def test_batch_failure_degrades_to_empty_map(self):
        def _boom(_ids):
            raise RuntimeError("batch down")

        assert resolve_metadata(["a1"], MetadataClient(
            get_video_info_batch=_boom,
            get_video_info=lambda url: {"id": "a1", "title": "A"},
        )) == {"a1": {"id": "a1", "title": "A"}}

    def test_resolve_metadata_does_not_refetch_present_ids(self):
        client = MetadataClient(
            get_video_info_batch=lambda ids: {"a1": {"title": "A"}},
            get_video_info=_unexpected,
            get_video_info_fallback=_unexpected,
        )
        assert resolve_metadata(["a1"], client) == {"a1": {"title": "A"}}

    def test_placeholder_used_when_all_lookups_fail(self):
        client = MetadataClient(
            get_video_info_batch=lambda ids: {},
            get_video_info=_unexpected,
            get_video_info_fallback=lambda _vid: None,
        )
        resolved = resolve_metadata(["a1"], client)
        assert resolved["a1"] == {
            "id": "a1", "title": None, "description": "",
            "url": "https://youtu.be/a1",
        }

    def test_channels_default_to_metadata_module_end_to_end(self, stub_metadata, tmp_path):
        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            metadata=MetadataClient(),
            download_video=_FakeDownload(),
            sleep=_FakeSleep(),
        )
        assert result["status"] is True
        assert result["result"]["total"] == 2


# ---------------------------------------------------------------------------
# _ZipCollector — dedup + thread safety
# ---------------------------------------------------------------------------


class TestZipCollector:
    def test_deduplicates_repeated_arcname(self, tmp_path):
        import zipfile

        collector = _ZipCollector(tmp_path / "out.zip")
        src = tmp_path / "a.bin"
        src.write_bytes(b"x")
        collector.add_file(str(src), "a/video.mp4")
        collector.add_file(str(src), "a/video.mp4")
        collector.finalize()
        with zipfile.ZipFile(tmp_path / "out.zip") as zf:
            assert zf.namelist() == ["a/video.mp4"]

    def test_finalize_without_adds_creates_empty_archive(self, tmp_path):
        import zipfile

        collector = _ZipCollector(tmp_path / "empty.zip")
        collector.finalize()
        with zipfile.ZipFile(tmp_path / "empty.zip") as zf:
            assert zf.namelist() == []

    def test_missing_source_file_is_tolerated(self, tmp_path):
        import zipfile

        collector = _ZipCollector(tmp_path / "out.zip")
        collector.add_file(str(tmp_path / "nope.bin"), "gone.bin")
        collector.finalize()
        with zipfile.ZipFile(tmp_path / "out.zip") as zf:
            assert zf.namelist() == []

    def test_concurrent_adds_keep_every_entry(self, tmp_path):
        import zipfile
        from concurrent.futures import ThreadPoolExecutor

        collector = _ZipCollector(tmp_path / "many.zip")
        sources = []
        for i in range(20):
            src = tmp_path / f"f{i}.bin"
            src.write_bytes(b"y")
            sources.append((str(src), f"f{i}.bin"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda pair: collector.add_file(*pair), sources))
        collector.finalize()
        with zipfile.ZipFile(tmp_path / "many.zip") as zf:
            assert sorted(zf.namelist()) == sorted(s for _p, s in sources)

    def test_full_mode_archive_has_no_duplicates(self, tmp_path):
        import zipfile

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path, concurrency=4),
            list_ids=_FakeIds(_video_ids("a1", "b2", "c3", "d4")),
            metadata=_FakeMeta(batch={
                "a1": {"title": "Satu"}, "b2": {"title": "Dua"},
                "c3": {"title": "Tiga"}, "d4": {"title": "Empat"},
            }),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        with zipfile.ZipFile(result["result"]["zipPath"]) as zf:
            names = zf.namelist()
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Default boundary helpers
# ---------------------------------------------------------------------------


class TestDefaultHelpers:
    def test_default_sleep_patches_time_sleep(self, monkeypatch):
        recorded = []
        monkeypatch.setattr("time.sleep", recorded.append)
        _default_sleep(0.25)
        assert recorded == [0.25]

    def test_default_download_video_uses_the_shared_downloader(self, monkeypatch):
        """RCH no longer has its own yt-dlp path; the harvester and the clipper
        share one downloader, so this asserts the delegation rather than any
        option pass-through (that is covered in test_channel_unified_download).
        """
        seen = {}

        class _Result:
            path = "p"
            title = "Judul"
            duration = 12.0
            video_id = "a"

        def _fake_factory():
            def _call(url, **kwargs):
                seen["url"] = url
                seen["kwargs"] = kwargs
                return _Result()

            return _call

        monkeypatch.setattr(channel_module, "_build_shared_downloader", _fake_factory)
        envelope = _default_download_video(
            "https://youtu.be/a", {"outputDir": "d", "filename": "video"}
        )

        assert envelope["status"] is True
        assert envelope["path"] == "p"
        assert seen["url"] == "https://youtu.be/a"
        assert seen["kwargs"]["output_dir"] == "d"

    def test_default_download_image_uses_core_http(self, monkeypatch):
        import rch.core.http as http_module

        seen = {}

        class _Resp:
            content = b"IMG"

        def _fake_get(url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return _Resp()

        monkeypatch.setattr(http_module, "http_get", _fake_get)
        assert _default_download_image("https://i.ytimg.com/vi/a/hqdefault.jpg") == b"IMG"
        assert seen["url"].endswith("hqdefault.jpg")
        assert seen["kwargs"]["timeout"] == _THUMBNAIL_TIMEOUT_SECONDS
        assert seen["kwargs"]["retries"] == _THUMBNAIL_RETRIES

    def test_channel_full_uses_default_download_video_when_unset(self, tmp_path, monkeypatch):
        def _fake(url, **kwargs):
            out_dir = Path(kwargs["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{kwargs.get('filename') or 'video'}.mp4"
            path.write_bytes(b"q" * 2)

            class _Result:
                def __init__(self):
                    self.path = path
                    self.title = "Satu"
                    self.duration = 5.0
                    self.video_id = "a1"

            return _Result()

        monkeypatch.setattr(
            channel_module, "_build_shared_downloader", lambda: _fake
        )
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["success"] == 1

    def test_channel_video_uses_default_sleep_when_unset(self, tmp_path, monkeypatch):
        recorded = []
        monkeypatch.setattr("time.sleep", recorded.append)
        channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_FakeDownload(failing=["a1"]),
        )
        assert recorded == [_RETRY_DELAY_SECONDS]

    def test_retry_exception_is_swallowed_and_recorded(self, tmp_path):
        class _Exploding:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, options=None):
                self.calls += 1
                if self.calls == 1:
                    return {"status": False, "message": "first pass failed"}
                raise RuntimeError("retry exploded")

        result = channel_video(
            CHANNEL_URL,
            _video_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "A"}}),
            download_video=_Exploding(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["failed"] == 1
        assert result["result"]["items"][0]["error"] == "first pass failed"

    def test_full_retry_exception_is_swallowed(self, tmp_path):
        class _Exploding:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, options=None):
                self.calls += 1
                if self.calls == 1:
                    return {"status": False, "message": "nope"}
                raise RuntimeError("retry exploded")

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_Exploding(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )
        assert result["result"]["failed"] == 1

    def test_thumbnail_bytes_like_payload_accepted(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=lambda url: bytearray(b"RAW"),
            sleep=_FakeSleep(),
        )
        thumb = Path(result["result"]["workDir"], "satu", "thumbnail.jpg")
        assert thumb.read_bytes() == b"RAW"

    def test_thumbnail_string_payload_encoded(self, tmp_path):
        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"}}),
            download_video=_FakeDownload(),
            download_image=lambda url: "TEXT",
            sleep=_FakeSleep(),
        )
        thumb = Path(result["result"]["workDir"], "satu", "thumbnail.jpg")
        assert thumb.read_bytes() == b"TEXT"


# ---------------------------------------------------------------------------
# build_report — aggregate report shaping
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_maps_full_result_fields(self):
        report = build_report({
            "channel": CHANNEL_URL, "total": 5, "success": 3, "failed": 2,
            "unavailable": 1, "unavailableIds": ["gone"],
            "zipPath": "out.zip", "items": [{"videoId": "a"}],
        })
        assert report["channel"] == CHANNEL_URL
        assert report["total"] == 5
        assert report["success"] == 3
        assert report["failed"] == 2
        assert report["unavailable"] == 1
        assert report["unavailableIds"] == ["gone"]
        assert report["zipPath"] == "out.zip"
        assert report["items"] == [{"videoId": "a"}]

    def test_defaults_for_missing_fields(self):
        report = build_report({})
        assert report["total"] == 0
        assert report["success"] == 0
        assert report["failed"] == 0
        assert report["unavailable"] == 0
        assert report["unavailableIds"] == []
        assert report["zipPath"] is None
        assert report["channel"] is None

    def test_report_is_writable_by_core_report(self, tmp_path):
        from rch.core.report import read_report, write_report

        result = channel_full(
            CHANNEL_URL,
            _full_opts(tmp_path),
            list_ids=_FakeIds(_video_ids("a1", "gone")),
            metadata=_FakeMeta(batch={"a1": {"title": "Satu"},
                                      "gone": {"title": None}}),
            download_video=_FakeDownload(),
            download_image=_FakeImage(),
            sleep=_FakeSleep(),
        )["result"]
        report = build_report(result)
        report["command"] = "channel-full"
        write_report(str(tmp_path), report)
        data = read_report(tmp_path)
        assert data["channel"] == CHANNEL_URL
        assert data["total"] == 2
        assert data["unavailable"] == 1
        assert "gone" in data["unavailableIds"]