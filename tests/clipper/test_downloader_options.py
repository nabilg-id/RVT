"""The options the harvester needs, added to the one downloader.

The harvester had quality selection, audio-only, subtitles and custom names.
Those moved here as keyword options rather than a second downloader, so the JS
runtime detection, the ffmpeg location, the fallback ladder and the cookie
chain are shared instead of duplicated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipper.services.youtube_downloader import (
    DownloadResult,
    YouTubeDownloader,
    build_format_filter,
    slugify,
)

from .test_youtube_downloader import URL, VIDEO_ID, FakeYDL, stub, write_output


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    """Same stubbed downloader the characterisation file uses."""
    return stub(monkeypatch, tmp_path)


def _opts(downloader, **kwargs):
    downloader.download(URL, **kwargs)
    return FakeYDL.seen[-1]


# -- format filter ----------------------------------------------------------


class TestBuildFormatFilter:
    def test_default_caps_at_1080p(self):
        assert build_format_filter("mp4", None)[0] == (
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        )

    def test_best_is_uncapped(self):
        assert build_format_filter("mp4", "best") == (
            "bestvideo+bestaudio/best", "mp4"
        )

    @pytest.mark.parametrize("quality,height", [
        ("360p", 360), ("480p", 480), ("720p", 720), ("1080p", 1080),
        ("2160p", 2160), ("1440", 1440),
    ])
    def test_quality_is_parsed_by_digits(self, quality, height):
        assert f"height<={height}" in build_format_filter("mp4", quality)[0]

    def test_nonsense_quality_falls_back_to_1080(self):
        assert "height<=1080" in build_format_filter("mp4", "ultra")[0]

    def test_audio_format_ignores_quality(self):
        assert build_format_filter("mp3", "360p") == ("bestaudio/best", "mp3")

    def test_custom_default_quality(self):
        assert "height<=720" in build_format_filter(
            "mp4", None, default_quality="720p"
        )[0]


# -- slugify ----------------------------------------------------------------


class TestSlugify:
    def test_lowercases_and_dashes_spaces(self):
        assert slugify("Judul Video Baru") == "judul-video-baru"

    def test_strips_dangerous_characters(self):
        assert slugify('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_keeps_dashes_and_underscores(self):
        assert slugify("clip-1_uji") == "clip-1_uji"

    def test_empty_falls_back(self):
        assert slugify("   ") == "video"
        assert slugify("...", fallback="id") == "id"

    def test_accents_are_folded_not_dropped(self):
        # NFKD decomposition is what saves the common case.
        assert slugify("Café Néon") == "cafe-neon"
        assert slugify("ÜBER Größe") == "uber-große".replace("ß", "")

    def test_untransliterable_letters_are_dropped(self):
        # Đ has no ASCII equivalent, so it is dropped rather than guessed at.
        assert slugify("Halo Đunia") == "halo-unia"

    def test_length_is_capped(self):
        assert len(slugify("a" * 300)) == 80

    def test_trailing_dash_is_trimmed_after_capping(self):
        assert not slugify("a" * 79 + " b").endswith("-")

    def test_runs_of_dashes_collapse(self):
        assert slugify("a    b") == "a-b"

    @pytest.mark.parametrize("name", [
        "con", "prn", "aux", "nul", "com1", "lpt9",
    ])
    def test_windows_device_names_are_escaped(self, name):
        """CON.mp4 is not a writable path on Windows."""
        assert slugify(name) == f"{name}-file"

    def test_dots_are_stripped_so_devices_cannot_hide_behind_one(self):
        # "nul.mp4" becomes "nulmp4", which is not a device name and needs no
        # escaping; what matters is that the result is writable.
        assert slugify("nul.mp4") == "nulmp4"


# -- output directory -------------------------------------------------------


class TestOutputDir:
    def test_default_goes_to_temp_dir(self, downloader, tmp_path):
        result = downloader.download(URL)
        assert result.path.parent == tmp_path

    def test_output_dir_override(self, downloader, tmp_path):
        target = tmp_path / "unduhan" / "sekarang"
        result = downloader.download(URL, output_dir=target)
        assert result.path.parent == target
        assert target.is_dir()

    def test_output_dir_is_created(self, downloader, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        downloader.download(URL, output_dir=target)
        assert target.is_dir()

    def test_custom_filename(self, downloader, tmp_path):
        result = downloader.download(URL, filename="Judul Video Baru")
        assert result.path.name == "judul-video-baru.mp4"

    def test_custom_filename_is_slugified(self, downloader, tmp_path):
        result = downloader.download(URL, filename="a/b:c")
        assert "/" not in result.path.name
        assert result.path.name == "abc.mp4"

    def test_filename_avoids_device_names(self, downloader, tmp_path):
        result = downloader.download(URL, filename="CON")
        assert result.path.name == "con-file.mp4"


# -- audio only -------------------------------------------------------------


class TestAudioOnly:
    def test_format_and_extension(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, ext="mp3")
        result = d.download(URL, audio_only=True)
        assert result.path.suffix == ".mp3"

    def test_postprocessor_is_configured(self, downloader):
        opts = _opts(downloader, audio_only=True)
        assert opts["format"] == "bestaudio/best"
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"

    def test_custom_audio_format(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, ext="m4a")
        result = d.download(URL, audio_only=True, audio_format="m4a")
        assert result.path.suffix == ".m4a"

    def test_audio_ignores_quality(self, downloader):
        opts = _opts(downloader, audio_only=True, quality="360p")
        assert "height" not in opts["format"]


# -- subtitles --------------------------------------------------------------


class TestSubtitles:
    def test_asks_for_both_manual_and_automatic(self, downloader):
        opts = _opts(downloader, subtitles=True)
        assert opts["writesubtitles"] is True
        assert opts["writeautomaticsub"] is True

    def test_language_is_forwarded(self, downloader):
        assert _opts(downloader, subtitles=True, sub_lang="id")[
            "subtitleslangs"
        ] == ["id"]

    def test_no_language_asks_for_all(self, downloader):
        assert _opts(downloader, subtitles=True)["subtitleslangs"] == ["all"]

    def test_subtitle_opts_absent_by_default(self, downloader):
        opts = _opts(downloader)
        assert "writesubtitles" not in opts
        assert "subtitleslangs" not in opts


# -- throttling and transport ----------------------------------------------


class TestTransportOptions:
    def test_limit_rate_is_converted(self, downloader):
        assert _opts(downloader, limit_rate="2M")["ratelimit"] == 2 * 1024 ** 2

    def test_limit_rate_accepts_k(self, downloader):
        assert _opts(downloader, limit_rate="500K")["ratelimit"] == 500 * 1024

    def test_limit_rate_accepts_plain_bytes(self, downloader):
        assert _opts(downloader, limit_rate="1000000")["ratelimit"] == 1000000

    def test_bad_limit_rate_is_ignored_not_fatal(self, downloader):
        """A typo in config must not stop a download that would otherwise work."""
        opts = _opts(downloader, limit_rate="lambat")
        assert "ratelimit" not in opts

    def test_proxy_is_forwarded(self, downloader):
        assert _opts(downloader, proxy="http://127.0.0.1:8080")[
            "proxy"
        ] == "http://127.0.0.1:8080"

    def test_proxy_absent_by_default(self, downloader):
        assert "proxy" not in _opts(downloader)

    def test_sleep_requests_is_forwarded(self, downloader):
        assert _opts(downloader, sleep_requests=1.5)[
            "sleep_interval_requests"
        ] == 1.5

    def test_progress_hook_is_registered(self, downloader):
        def hook(status):
            return None

        assert _opts(downloader, progress_hook=hook)["progress_hooks"] == [hook]

    def test_max_retries_is_passed_through(self, downloader):
        opts = _opts(downloader, max_retries=3)
        assert opts["retries"] == 3
        assert opts["fragment_retries"] == 3
        assert opts["extractor_retries"] == 3


class TestPerCallCookieOverride:
    def test_argument_beats_config(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, cookies_browser="firefox")
        d.download(URL, cookies_browser="edge")
        assert FakeYDL.seen[0]["cookiesfrombrowser"] == ("edge",)

    def test_config_used_when_no_argument(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, cookies_browser="firefox")
        d.download(URL)
        assert FakeYDL.seen[0]["cookiesfrombrowser"] == ("firefox",)


# -- result object ----------------------------------------------------------


class TestDownloadResult:
    def test_carries_every_field(self, downloader):
        result = downloader.download(URL)
        assert isinstance(result, DownloadResult)
        assert result.video_id == VIDEO_ID
        assert result.title == "Judul Video"
        assert result.duration == 212
        assert result.files

    def test_unpacks_like_the_old_tuple(self, downloader):
        path, title, duration = downloader.download(URL)
        assert title == "Judul Video"
        assert duration == 212

    def test_supports_indexing(self, downloader):
        result = downloader.download(URL)
        assert result[1] == "Judul Video"
        assert len(result) == 3

    def test_files_lists_what_was_written(self, downloader, tmp_path):
        result = downloader.download(URL)
        assert result.path in result.files
        assert all(Path(f).exists() for f in result.files)

    def test_path_is_a_path_object(self, downloader):
        assert isinstance(downloader.download(URL).path, Path)


# -- locating the output ----------------------------------------------------


class TestLocateOutput:
    def test_prefers_the_requested_extension(self, downloader, tmp_path):
        write_output({"outtmpl": str(tmp_path / f"{VIDEO_ID}.%(ext)s")})
        (tmp_path / f"{VIDEO_ID}.mp4").write_bytes(b"\x00")
        (tmp_path / f"{VIDEO_ID}.webm").write_bytes(b"\x00")
        result = downloader.download(URL)
        assert result.path.suffix == ".mp4"

    def test_falls_back_to_any_matching_file(self, downloader, tmp_path):
        """A merge can end up as webm even when mp4 was requested."""
        (tmp_path / f"{VIDEO_ID}.webm").write_bytes(b"\x00")

        class OnlyWebm(FakeYDL):
            def extract_info(self, url, download=True):
                return {"title": "T", "duration": 1}

        from clipper.services import youtube_downloader as Y

        original = Y.yt_dlp.YoutubeDL
        Y.yt_dlp.YoutubeDL = OnlyWebm
        try:
            result = downloader.download(URL)
        finally:
            Y.yt_dlp.YoutubeDL = original
        assert result.path.suffix == ".webm"

    def test_no_output_raises(self, downloader):
        from clipper.services import youtube_downloader as Y

        class Nothing(FakeYDL):
            def extract_info(self, url, download=True):
                return {"title": "T", "duration": 1}

        original = Y.yt_dlp.YoutubeDL
        Y.yt_dlp.YoutubeDL = Nothing
        try:
            with pytest.raises(FileNotFoundError):
                downloader.download(URL)
        finally:
            Y.yt_dlp.YoutubeDL = original


class TestBackwardCompatibility:
    def test_positional_url_still_works(self, downloader):
        """The clip pipeline calls download(url) with no keywords."""
        assert downloader.download(URL).video_id == VIDEO_ID

    def test_clip_pipeline_default_options_unchanged(self, downloader):
        """What video_processor relies on: retries of ten, merge to mp4, errors
        not ignored."""
        opts = _opts(downloader)
        assert opts["retries"] == 10
        assert opts["merge_output_format"] == "mp4"
        assert opts["ignoreerrors"] is False
        assert opts["outtmpl"].endswith(f"{VIDEO_ID}.%(ext)s")

    def test_downloader_class_signature_is_still_constructible(self, tmp_path):
        d = YouTubeDownloader(temp_dir=tmp_path)
        assert d.temp_dir == tmp_path