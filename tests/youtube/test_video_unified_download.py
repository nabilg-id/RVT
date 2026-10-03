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
