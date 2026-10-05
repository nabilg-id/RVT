"""Tests for rch.youtube.video delegating to the shared clipper downloader.

``video.download`` was the last place still shelling out to yt-dlp itself. It
had its own retry ladder, its own argument builder and its own cookie handling,
all of which the clipper's downloader already does - and does with a JavaScript
runtime, imageio's ffmpeg and a three-tier player-client fallback.

The seam is the injected runner. A caller that passes ``run_ytdlp`` wants the
subprocess path and keeps it, which is what the existing suite does. A caller
that passes nothing - the CLI, ``/api/download`` - gets the shared downloader.
"""
from __future__ import annotations

from rch.youtube import video as V

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class FakeResult:
    def __init__(self, path="downloads/x/video.mp4", title="Judul",
                 duration=61.0, video_id="dQw4w9WgXcQ"):
        self.path = path
        self.title = title
        self.duration = duration
        self.video_id = video_id
        self.files = []


class Recorder:
    """Stands in for YouTubeDownloader."""

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
        "outputDir": "downloads",
        "filename": "judul",
        "retries": 0,
    }
    base.update(overrides)
    return base


class TestItUsesTheSharedDownloader:
    def test_the_url_is_forwarded(self):
        rec = Recorder()

        V.download(URL, _opts(), downloader=rec)

        assert rec.calls[0][0] == URL

    def test_output_dir_is_translated(self):
        """Passing RCH's spelling straight through would raise TypeError for
        every option, because the downloader takes keyword-only arguments."""
        rec = Recorder()

        V.download(URL, _opts(), downloader=rec)

        assert rec.calls[0][1]["output_dir"] == "downloads"

    def test_quality_and_filename_pass_through(self):
        rec = Recorder()

        V.download(URL, _opts(quality="1080p"), downloader=rec)

        kwargs = rec.calls[0][1]
        assert kwargs["quality"] == "1080p"
        assert kwargs["filename"] == "judul"

    def test_mp3_switches_to_audio_only(self):
        rec = Recorder()

        V.download(URL, _opts(format="mp3"), downloader=rec)

        kwargs = rec.calls[0][1]
        assert kwargs["audio_only"] is True
        assert kwargs["audio_format"] == "mp3"

    def test_mp4_is_not_audio_only(self):
        rec = Recorder()

        V.download(URL, _opts(format="mp4"), downloader=rec)

        assert rec.calls[0][1]["audio_only"] is False

    def test_partial_options_do_not_raise(self):
        rec = Recorder()

        V.download(URL, {"retries": 0}, downloader=rec)

        assert rec.calls[0][0] == URL


class TestEnvelope:
    def test_success_carries_the_path(self):
        rec = Recorder(FakeResult(path="downloads/judul/video.mp4",
                                  title="Judul"))

        envelope = V.download(URL, _opts(), downloader=rec)

        assert envelope["status"] is True
        assert "video.mp4" in str(envelope["result"]["path"])
        assert envelope["result"]["title"] == "Judul"

    def test_failure_is_reported_not_raised(self):
        """download never raises; its callers treat the envelope as the result."""
        rec = Recorder(error=RuntimeError("403 Forbidden"))

        envelope = V.download(URL, _opts(), downloader=rec)

        assert envelope["status"] is False
        assert "403" in envelope["message"]

    def test_an_empty_error_still_says_something(self):
        rec = Recorder(error=RuntimeError())

        envelope = V.download(URL, _opts(), downloader=rec)

        assert envelope["status"] is False
        assert envelope["message"]


class TestNoDoubleRetry:
    def test_the_outer_ladder_does_not_retry_a_shared_failure(self):
        """The shared downloader already retries three ways with player-client
        fallbacks. Retrying the whole call on top of that turns one bad video
        into a dozen requests and a much longer wait."""
        rec = Recorder(error=RuntimeError("unavailable"))

        V.download(URL, _opts(retries=3), downloader=rec)

        assert len(rec.calls) == 1


class TestTheInjectedRunnerStillWorks:
    """The seam has to hold for the existing subprocess suite."""

    def test_an_injected_runner_is_used_instead_of_the_shared_downloader(self):
        seen = []

        def _run(args, **kwargs):
            seen.append(args)
            return '{"_type": "video", "id": "dQw4w9WgXcQ", "title": "T",\n' \
                   ' "duration": 5, "webpage_url": "u", "formats": []}'

        envelope = V.download(URL, _opts(retries=0), run_ytdlp=_run,
                              fetch_title=lambda vid: "T")

        assert seen, "subprocess path was not taken"
        assert "status" in envelope

    def test_the_shared_downloader_is_not_built_when_a_runner_is_given(self):
        def _explode(*a, **k):
            raise AssertionError("the shared downloader should not be used")

        V.download(URL, _opts(retries=0),
                   run_ytdlp=lambda args, **k: '{"id": "dQw4w9WgXcQ"}',
                   fetch_title=lambda vid: "T",
                   downloader=_explode)


class TestTrackingStillHappens:
    def test_a_failure_is_recorded_in_the_ledger(self, tmp_path, monkeypatch):
        import rch.core.tracker as tracker_mod

        seen = []
        monkeypatch.setenv("VIDEO_TRACKER_FILE", str(tmp_path / "t.jsonl"))
        monkeypatch.setattr(tracker_mod, "append_event",
                            lambda *a, **k: seen.append((a, k)))

        V.download(URL, _opts(), downloader=Recorder(error=RuntimeError("x")))

        assert seen
        assert seen[0][1]["status"] == "failed"


class TestStandaloneFallback:
    """The production path when no downloader is injected.

    Every other test in this file injects a stand-in, which is right for testing
    the translation but leaves the code that actually runs in production
    uncovered: RCH on its own has to find the shared downloader by itself, and
    when that import fails it has to report the failure rather than raise.

    The shared downloader is faked through ``sys.modules`` rather than patched,
    for the same reason the clipper tests fake ``video_processor`` that way:
    importing the real module pulls in yt_dlp, which is not importable on every
    interpreter this project supports, and a test must not depend on that.
    """

    def test_it_builds_the_shared_downloader_when_none_is_given(
        self, monkeypatch, tmp_path
    ):
        import sys
        import types

        built = {}

        class FakeResult2:
            path = "downloads/x/video.mp4"
            title = "Judul"
            duration = 61.0
            video_id = "dQw4w9WgXcQ"

        class FakeDownloader:
            def __init__(self, *a, **k):
                built["created"] = True

            def download(self, url, **kwargs):
                built["call"] = (url, kwargs)
                return FakeResult2()

        module = types.ModuleType("clipper.services.youtube_downloader")
        module.YouTubeDownloader = FakeDownloader
        monkeypatch.setitem(sys.modules, "clipper.services.youtube_downloader",
                            module)

        result = V.download(URL, _opts(), downloader=None)

        assert built.get("created") is True
        assert built["call"][0] == URL
        assert result["status"] is True

    def test_a_downloader_that_cannot_be_built_is_reported_not_raised(
        self, monkeypatch, tmp_path
    ):
        import sys
        import types

        import rch.core.tracker as tracker_mod

        monkeypatch.setattr(tracker_mod, "append_event", lambda *a, **k: None)

        class Exploding:
            def __init__(self, *a, **k):
                raise RuntimeError("tidak bisa menyiapkan downloader")

        module = types.ModuleType("clipper.services.youtube_downloader")
        module.YouTubeDownloader = Exploding
        monkeypatch.setitem(sys.modules, "clipper.services.youtube_downloader",
                            module)

        result = V.download(URL, _opts(), downloader=None)

        assert result["status"] is False
        assert "tidak bisa menyiapkan downloader" in result["message"]


class TestSharedKwargs:
    """Every RCH option has to reach the downloader under its own name."""

    def test_audio_format_follows_an_audio_only_request(self):
        kwargs = V._shared_kwargs({"format": "MP3"})
        assert kwargs["audio_only"] is True
        assert kwargs["audio_format"] == "mp3"

    def test_video_format_is_not_audio_only(self):
        assert V._shared_kwargs({"format": "mp4"})["audio_only"] is False

    def test_missing_format_defaults_to_mp4(self):
        assert V._shared_kwargs({}) == {"audio_only": False}

    def test_quality_and_output_dir_are_renamed(self):
        kwargs = V._shared_kwargs({"quality": "1080p", "outputDir": "keluar"})
        assert kwargs["quality"] == "1080p"
        assert kwargs["output_dir"] == "keluar"

    def test_filename_is_forwarded(self):
        assert V._shared_kwargs({"filename": "judul"})["filename"] == "judul"

    def test_subtitles_carry_their_language(self):
        kwargs = V._shared_kwargs({"subtitles": True, "subLang": "id"})
        assert kwargs["subtitles"] is True
        assert kwargs["sub_lang"] == "id"

    def test_subtitles_without_a_language(self):
        kwargs = V._shared_kwargs({"subtitles": True})
        assert kwargs["sub_lang"] is None

    def test_cookies_browser_limit_rate_and_proxy(self):
        kwargs = V._shared_kwargs({
            "cookiesBrowser": "firefox",
            "limitRate": "2M",
            "proxy": "http://proxy",
        })
        assert kwargs["cookies_browser"] == "firefox"
        assert kwargs["limit_rate"] == "2M"
        assert kwargs["proxy"] == "http://proxy"

    def test_sleep_requests_of_zero_is_still_forwarded(self):
        """Zero is a real instruction - no delay between requests - so it must
        not be dropped the way a falsy check would drop it."""
        assert V._shared_kwargs({"sleepRequests": 0})["sleep_requests"] == 0

    def test_absent_sleep_requests_is_omitted(self):
        assert "sleep_requests" not in V._shared_kwargs({})
