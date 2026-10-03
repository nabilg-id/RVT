"""Tests for the harvester's delegation to the shared clipper downloader.

RCH and the clipper used to download through two separate implementations. The
decision is one download path: ``clipper.services.youtube_downloader``. These
tests pin the adapter that translates RCH's option dict into that downloader's
keyword arguments, because a silent mistranslation would download 360p audio
into the wrong folder and still report success.
"""
from __future__ import annotations

from rch.youtube import channel as C

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class FakeResult:
    def __init__(self, path="clips/x.mp4", title="Judul", duration=61.0):
        self.path = path
        self.title = title
        self.duration = duration
        self.video_id = "dQw4w9WgXcQ"
        self.files = []


class Recorder:
    """Stands in for YouTubeDownloader and remembers how it was called."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result if result is not None else FakeResult()
        self._error = error

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._error is not None:
            raise self._error
        return self._result


def _opts(**overrides):
    base = {
        "format": "mp4",
        "quality": "720p",
        "outputDir": "downloads/abc",
        "filename": "video",
        "subtitles": False,
        "subLang": "en",
    }
    base.update(overrides)
    return base


class TestOptionTranslation:
    def test_output_dir_is_renamed_not_dropped(self):
        """The snake_case name is the clipper's; passing outputDir through
        would be a TypeError the caller never sees."""
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert rec.calls[0][1]["output_dir"] == "downloads/abc"

    def test_quality_and_filename_pass_through(self):
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        kwargs = rec.calls[0][1]
        assert kwargs["quality"] == "720p"
        assert kwargs["filename"] == "video"

    def test_subtitle_options_pass_through(self):
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(subtitles=True, subLang="id"),
                                  downloader=rec)

        kwargs = rec.calls[0][1]
        assert kwargs["subtitles"] is True
        assert kwargs["sub_lang"] == "id"

    def test_video_format_is_not_audio_only(self):
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(format="mp4"), downloader=rec)

        assert rec.calls[0][1]["audio_only"] is False

    def test_mp3_format_switches_to_audio_only(self):
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(format="mp3"), downloader=rec)

        kwargs = rec.calls[0][1]
        assert kwargs["audio_only"] is True
        assert kwargs["audio_format"] == "mp3"

    def test_the_url_is_forwarded_unchanged(self):
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert rec.calls[0][0] == VIDEO_URL

    def test_absent_options_do_not_become_errors(self):
        """Partial option dicts are normal; they must not raise TypeError."""
        rec = Recorder()

        C._default_download_video(VIDEO_URL, {}, downloader=rec)

        assert rec.calls[0][0] == VIDEO_URL


class TestEnvelope:
    def test_success_is_reported_with_the_path(self):
        rec = Recorder(FakeResult(path="downloads/abc/video.mp4", title="Judul"))

        envelope = C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert envelope["status"] is True
        assert envelope["title"] == "Judul"
        assert "video.mp4" in str(envelope["path"])

    def test_the_nested_result_path_is_kept(self):
        """The channel workers and the CLI read ``result.path``; dropping it
        would turn every download into a KeyError reported as a failed video."""
        rec = Recorder(FakeResult(path="downloads/abc/video.mp4"))

        envelope = C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert envelope["result"]["path"] == envelope["path"]

    def test_a_download_failure_becomes_a_false_envelope_not_an_exception(self):
        """channel_full calls this inside a per-video worker; a raise here
        would be caught as a crash rather than recorded as one bad video."""
        rec = Recorder(error=RuntimeError("403 Forbidden"))

        envelope = C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert envelope["status"] is False
        assert "403" in envelope["message"]

    def test_an_empty_error_message_still_says_something(self):
        rec = Recorder(error=RuntimeError())

        envelope = C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        assert envelope["status"] is False
        assert envelope["message"]


class TestOnePathOnly:
    def test_no_rch_video_module_import_is_needed(self):
        """The whole point: the harvester must not reach for its own
        yt-dlp wrapper anymore."""
        rec = Recorder()

        C._default_download_video(VIDEO_URL, _opts(), downloader=rec)

        # If the adapter still called rch.youtube.video.download, the fake would
        # not have been called at all and this would be zero.
        assert len(rec.calls) == 1

    def test_a_missing_clipper_downloader_is_reported_not_raised(self):
        """RCH is usable standalone; without the clipper installed the failure
        must be an envelope the UI can show."""
        def _boom(_url, **_kwargs):
            raise AssertionError("should not be reached")

        envelope = C._default_download_video(
            VIDEO_URL, _opts(), downloader_factory=_boom
        )

        assert envelope["status"] is False
        assert envelope["message"]


class TestDefaults:
    def test_no_injected_downloader_builds_the_real_one(self, tmp_path):
        """Without the fake, the real YouTubeDownloader is constructed. This
        must not raise; the network attempt inside is what fails instead."""
        envelope = C._default_download_video(
            "not-a-url", _opts(outputDir=str(tmp_path))
        )

        assert "status" in envelope
