"""Tests for rch.youtube.video — download logic.

Covers the retry classifier (critical: controls retry storms), format/quality
filter construction, argument building, and the retry/backoff orchestration.
All external boundaries are injected fakes — no network, no subprocess.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rch.youtube.video import (
    build_args,
    build_format_filter,
    download,
    download_once,
    get_title,
    is_retryable_error,
)

WATCH_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# is_retryable_error — transient-failure classifier
# ---------------------------------------------------------------------------


class TestIsRetryableError:
    @pytest.mark.parametrize(
        "message",
        [
            "HTTP Error 403: Forbidden",
            "HTTP Error 429: Too Many Requests",
            "forbidden",
            "too many requests",
            "ECONNRESET",
            "ETIMEDOUT",
            "Read timed out",
            "ECONNREFUSED",
            "network is unreachable",
            "unable to download video data",
        ],
    )
    def test_detects_retryable_messages(self, message):
        assert is_retryable_error(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "Invalid YouTube video URL.",
            "Download selesai tapi file tidak ditemukan.",
            "",
            "Invalid thumbnail size",
        ],
    )
    def test_rejects_non_retryable_messages(self, message):
        assert is_retryable_error(message) is False

    def test_none_message_is_not_retryable(self):
        assert is_retryable_error(None) is False

    def test_is_case_insensitive(self):
        assert is_retryable_error("HTTP ERROR 403: FORBIDDEN") is True

    def test_matching_is_substring_based(self):
        # Any occurrence counts, not just a prefix.
        assert is_retryable_error("prefix: 403 suffix") is True


# ---------------------------------------------------------------------------
# build_format_filter — format/quality mapping
# ---------------------------------------------------------------------------


class TestBuildFormatFilter:
    def test_mp3_uses_bestaudio_and_mp3_extension(self):
        fmt, ext = build_format_filter("mp3", "720p")
        assert fmt == "bestaudio/best"
        assert ext == "mp3"

    def test_mp3_ignores_quality(self):
        assert build_format_filter("mp3", "best") == build_format_filter("mp3", "1080p")

    def test_best_quality_uses_bestvideo_plus_bestaudio(self):
        fmt, ext = build_format_filter("mp4", "best")
        assert fmt == "bestvideo+bestaudio/best"
        assert ext == "mp4"

    def test_numeric_quality_caps_height(self):
        fmt, _ = build_format_filter("mp4", "480p")
        assert "height<=480" in fmt
        assert "bestvideo" in fmt and "bestaudio" in fmt
        assert fmt.endswith("/best")

    def test_format_is_case_insensitive(self):
        assert build_format_filter("MP3", "720p")[1] == "mp3"
        assert build_format_filter("MP4", "720p")[1] == "mp4"

    @pytest.mark.parametrize(
        "quality,height",
        [("720p", "720"), ("1080p", "1080"), ("360", "360"), ("", "720"), ("abc", "720")],
    )
    def test_height_extraction_from_quality(self, quality, height):
        fmt, _ = build_format_filter("mp4", quality)
        assert f"height<={height}" in fmt

    def test_unknown_format_falls_back_to_mp4(self):
        _, ext = build_format_filter("webm", "720p")
        assert ext == "mp4"


# ---------------------------------------------------------------------------
# get_title — oEmbed lookup with graceful degradation
# ---------------------------------------------------------------------------


class TestGetTitle:
    def test_returns_title_from_oembed(self):
        captured = {}

        def fake_get(url):
            captured["url"] = url
            return '{"title": "Never Gonna Give You Up"}'

        assert get_title(VIDEO_ID, http_get=fake_get) == "Never Gonna Give You Up"
        assert VIDEO_ID in captured["url"]
        assert "oembed" in captured["url"]

    def test_falls_back_to_video_id_when_http_raises(self):
        def boom(_url):
            raise ConnectionError("no network")

        assert get_title(VIDEO_ID, http_get=boom) == VIDEO_ID

    def test_falls_back_to_video_id_when_payload_has_no_title(self):
        assert get_title(VIDEO_ID, http_get=lambda _u: "{}") == VIDEO_ID

    def test_falls_back_to_video_id_when_payload_is_not_json(self):
        assert get_title(VIDEO_ID, http_get=lambda _u: "<html>nope</html>") == VIDEO_ID

    def test_falls_back_to_video_id_when_title_is_empty_string(self):
        assert get_title(VIDEO_ID, http_get=lambda _u: '{"title": ""}') == VIDEO_ID

    def test_falls_back_to_video_id_on_malformed_json(self):
        assert get_title(VIDEO_ID, http_get=lambda _u: "{bad json}") == VIDEO_ID


# ---------------------------------------------------------------------------
# build_args — yt-dlp argument construction
# ---------------------------------------------------------------------------


class TestBuildArgs:
    def test_builds_expected_argument_order(self):
        args = build_args(WATCH_URL, VIDEO_ID, "my-video", {}, "downloads")
        assert args[0] == "--no-playlist"
        assert args[1] == "-f"
        assert args[-1] == WATCH_URL
        assert "--merge-output-format" in args
        assert "mp4" in args

    def test_output_template_uses_safe_title_and_ext(self):
        args = build_args(WATCH_URL, VIDEO_ID, "my-video", {}, "downloads")
        idx = args.index("-o")
        template = args[idx + 1]
        assert template.endswith("my-video.%(ext)s")
        assert "downloads" in template

    def test_mp3_adds_extract_audio_flags(self):
        args = build_args(WATCH_URL, VIDEO_ID, "my-audio", {"format": "mp3"}, "downloads")
        assert "--extract-audio" in args
        assert args[args.index("--audio-format") + 1] == "mp3"
        assert "--merge-output-format" not in args

    def test_subtitles_flag_adds_subs_options(self):
        args = build_args(WATCH_URL, VIDEO_ID, "t", {"subtitles": True}, "downloads")
        assert "--write-subs" in args
        assert "--write-auto-subs" in args
        assert args[args.index("--sub-lang") + 1] == "all"

    def test_subtitles_custom_language(self):
        args = build_args(
            WATCH_URL, VIDEO_ID, "t",
            {"subtitles": True, "subLang": "en,id"}, "downloads",
        )
        assert args[args.index("--sub-lang") + 1] == "en,id"

    def test_no_subtitle_flags_when_not_requested(self):
        args = build_args(WATCH_URL, VIDEO_ID, "t", {}, "downloads")
        assert "--write-subs" not in args

    def test_respects_custom_quality(self):
        args = build_args(WATCH_URL, VIDEO_ID, "t", {"quality": "480p"}, "downloads")
        format_filter = args[args.index("-f") + 1]
        assert "height<=480" in format_filter


# ---------------------------------------------------------------------------
# download_once — single attempt orchestration
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Records yt-dlp calls and optionally writes the resulting file."""

    def __init__(self, *, errors=None, create_file=True, ext="mp4"):
        self.calls = []
        self._errors = list(errors or [])
        self._create_file = create_file
        self._ext = ext

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self._errors:
            raise RuntimeError(self._errors.pop(0))
        if self._create_file:
            template = args[args.index("-o") + 1]
            rendered = template.replace("%(ext)s", self._ext)
            path = Path(rendered)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"data" * 10)
        return ""


class TestDownloadOnce:
    def test_returns_success_envelope(self, tmp_path):
        runner = _FakeRunner()
        result = download_once(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Never Gonna Give You Up",
        )
        assert result["status"] is True
        r = result["result"]
        assert r["id"] == VIDEO_ID
        assert r["title"] == "never-gonna-give-you-up"
        assert r["format"] == "mp4"
        assert r["sizeBytes"] == 40
        assert Path(r["path"]).exists()

    def test_invalid_url_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid YouTube video URL"):
            download_once("https://example.com/nope", {"outputDir": str(tmp_path)})

    def test_explicit_filename_skips_title_lookup(self, tmp_path):
        runner = _FakeRunner()
        result = download_once(
            WATCH_URL,
            {"outputDir": str(tmp_path), "filename": "My Custom Name"},
            run_ytdlp=runner,
            fetch_title=lambda _id: pytest.fail("title should not be fetched"),
        )
        assert result["result"]["title"] == "my-custom-name"

    def test_raises_when_output_file_missing(self, tmp_path):
        runner = _FakeRunner(create_file=False)
        with pytest.raises(ValueError, match="file tidak ditemukan"):
            download_once(
                WATCH_URL,
                {"outputDir": str(tmp_path)},
                run_ytdlp=runner,
                fetch_title=lambda _id: "Title",
            )

    def test_creates_output_directory(self, tmp_path):
        target = tmp_path / "nested" / "deeper"
        runner = _FakeRunner()
        download_once(
            WATCH_URL,
            {"outputDir": str(target)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "T",
        )
        assert target.is_dir()

    def test_mp3_download_reports_mp3_extension(self, tmp_path):
        runner = _FakeRunner(ext="mp3")
        result = download_once(
            WATCH_URL,
            {"outputDir": str(tmp_path), "format": "mp3"},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Audio",
        )
        assert result["result"]["path"].endswith(".mp3")
        assert result["result"]["format"] == "mp3"

    def test_title_fetch_failure_falls_back_to_video_id(self, tmp_path):
        runner = _FakeRunner()
        result = download_once(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "",
        )
        assert result["result"]["title"] == VIDEO_ID


# ---------------------------------------------------------------------------
# download — retry/backoff orchestration
# ---------------------------------------------------------------------------


class TestDownload:
    def test_success_on_first_attempt(self, tmp_path):
        runner = _FakeRunner()
        sleeps = []
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=sleeps.append,
        )
        assert result["status"] is True
        assert len(runner.calls) == 1
        assert sleeps == []

    def test_retries_retryable_error_then_succeeds(self, tmp_path):
        runner = _FakeRunner(errors=["HTTP Error 403: Forbidden"])
        sleeps = []
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=sleeps.append,
        )
        assert result["status"] is True
        assert len(runner.calls) == 2
        assert sleeps == [2.0]

    def test_exponential_backoff_between_attempts(self, tmp_path):
        runner = _FakeRunner(
            errors=["HTTP Error 429: Too Many Requests", "Read timed out"]
        )
        sleeps = []
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path), "retries": 2},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=sleeps.append,
        )
        assert result["status"] is True
        assert sleeps == [2.0, 4.0]

    def test_does_not_retry_non_retryable_error(self, tmp_path):
        runner = _FakeRunner(errors=["Invalid YouTube video URL."])
        sleeps = []
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=sleeps.append,
        )
        assert result["status"] is False
        assert "Invalid YouTube video URL." in result["message"]
        assert len(runner.calls) == 1
        assert sleeps == []

    def test_exhausts_retries_then_reports_failure(self, tmp_path):
        runner = _FakeRunner(errors=["HTTP Error 403: Forbidden"] * 10)
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path), "retries": 1},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=lambda _s: None,
        )
        assert result["status"] is False
        assert "403" in result["message"]
        assert len(runner.calls) == 2

    def test_zero_retries_means_single_attempt(self, tmp_path):
        runner = _FakeRunner(errors=["HTTP Error 403: Forbidden"] * 10)
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path), "retries": 0},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=lambda _s: None,
        )
        assert result["status"] is False
        assert len(runner.calls) == 1

    def test_ensure_updated_failure_returns_failure_envelope(self, tmp_path):
        def boom():
            raise RuntimeError("yt-dlp not found")

        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=_FakeRunner(),
            fetch_title=lambda _id: "T",
            ensure_updated=boom,
            sleep=lambda _s: None,
        )
        assert result["status"] is False
        assert "yt-dlp not found" in result["message"]

    def test_ensure_updated_runs_before_download(self, tmp_path):
        order = []

        def ensure():
            order.append("ensure")

        def fake_run(args, **kwargs):
            order.append("download")
            return _FakeRunner()(args, **kwargs)

        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=fake_run,
            fetch_title=lambda _id: "T",
            ensure_updated=ensure,
            sleep=lambda _s: None,
        )
        assert result["status"] is True
        assert order == ["ensure", "download"]

    def test_invalid_url_returns_failure_envelope(self):
        result = download("https://example.com/nope")
        assert result["status"] is False
        assert "Invalid YouTube video URL." in result["message"]

    def test_defaults_to_retries_of_two_when_unspecified(self, tmp_path):
        runner = _FakeRunner(errors=["HTTP Error 403: Forbidden"] * 10)
        result = download(
            WATCH_URL,
            {"outputDir": str(tmp_path)},
            run_ytdlp=runner,
            fetch_title=lambda _id: "Title",
            sleep=lambda _s: None,
        )
        assert result["status"] is False
        assert len(runner.calls) == 3  # initial + 2 retries