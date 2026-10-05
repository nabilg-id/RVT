"""Characterisation tests for the one downloader everything depends on.

The clipper's whole pipeline starts at YouTubeDownloader.download, and until
this file existed there was not a single test covering it. That is how the
transcription breakage went unnoticed: nothing here was exercised at all.

These tests pin the behaviour that must survive the upcoming upgrade - the
format string, the retry counts, the cookie precedence chain, the player-client
fallback ladder, and the return contract - so a refactor that quietly drops one
of them fails here instead of in production.

yt_dlp is stubbed at the YoutubeDL boundary, so nothing spawns a process or
touches the network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipper.services import youtube_downloader as Y

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}"


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL.

    Records the opts it was built with and replays a scripted sequence of
    results, one per construction, so the fallback ladder can be walked.
    """

    script: list = []
    seen: list = []
    #: Extension the fake writes; audio-only tests flip this to mp3/m4a.
    ext: str = "mp4"

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        step = len(FakeYDL.seen) - 1
        action = FakeYDL.script[step] if step < len(FakeYDL.script) else "ok"
        if action == "ok":
            write_output(self.opts, FakeYDL.ext)
            return {"title": "Judul Video", "duration": 212}
        import yt_dlp

        raise yt_dlp.utils.DownloadError(action)


def write_output(opts: dict, ext: str = "mp4"):
    """Create the file yt-dlp would have written, so the post-download check
    finds it.

    The name is derived from ``outtmpl`` because a caller-supplied filename
    changes the stem, and the extension is passed in because an audio-only
    download ends as ``.mp3`` rather than ``.mp4``.
    """
    out = opts["outtmpl"]
    template = Path(out[: out.index(".%(ext)s")])
    target = template.with_name(template.name + "." + ext)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * 128)


def stub(monkeypatch, tmp_path, *, js_runtimes=None, cookies_file=None,
         cookies_content=None, cookies_browser=None, user_agent=None,
         script=None, ext=None):
    """Wire every external boundary of the downloader to a stub.

    ``_get_js_runtimes`` is a method, so it is patched on the class rather than
    on the module.
    """
    FakeYDL.seen = []
    FakeYDL.script = script if script is not None else ["ok"]
    FakeYDL.ext = ext or "mp4"

    monkeypatch.setattr(Y, "COOKIES_FILE",
                        cookies_file if cookies_file is not None
                        else tmp_path / "tidak-ada-cookies.txt")
    monkeypatch.setattr(Y, "YOUTUBE_COOKIES_CONTENT", cookies_content)
    monkeypatch.setattr(Y, "YOUTUBE_COOKIES_BROWSER", cookies_browser)
    monkeypatch.setattr(Y, "YOUTUBE_USER_AGENT", user_agent)
    monkeypatch.setattr(Y.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(
        Y.YouTubeDownloader, "_get_js_runtimes",
        lambda self: js_runtimes if js_runtimes is not None else {},
    )
    monkeypatch.setattr(Y.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/fake/ffmpeg")
    return Y.YouTubeDownloader(temp_dir=tmp_path)


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    return stub(monkeypatch, tmp_path)


# -- video id ---------------------------------------------------------------


class TestGetVideoId:
    @pytest.mark.parametrize("url,expected", [
        (f"https://www.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
        (f"https://youtu.be/{VIDEO_ID}", VIDEO_ID),
        (f"https://youtube.com/watch?v={VIDEO_ID}&t=30s", VIDEO_ID),
        (f"https://m.youtube.com/watch?v={VIDEO_ID}", VIDEO_ID),
    ])
    def test_recognised_forms(self, url, expected):
        assert Y.YouTubeDownloader.get_video_id(url) == expected

    @pytest.mark.parametrize("url", [
        "", "https://vimeo.com/12345", "not a url", VIDEO_ID,
    ])
    def test_unrecognised_returns_none(self, url):
        assert Y.YouTubeDownloader.get_video_id(url) is None


# -- validation -------------------------------------------------------------


class TestValidation:
    def test_invalid_url_raises_value_error(self, downloader):
        with pytest.raises(ValueError, match="Invalid YouTube URL"):
            downloader.download("https://vimeo.com/12345")


# -- the options contract ---------------------------------------------------


class TestOptions:
    def _run(self, downloader, url=None):
        downloader.download(url or URL)
        return FakeYDL.seen[0]

    def test_format_caps_at_1080p_by_default(self, downloader):
        """Deliberate change from uncapped 'best': clips are 9:16 and a 4K
        source only made a 19-second job take twelve minutes."""
        assert FakeYDL.seen == []
        downloader.download(URL)
        assert FakeYDL.seen[0]["format"] == (
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        )

    def test_explicit_best_keeps_the_uncapped_format(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path)
        d.download(URL, quality="best")
        assert FakeYDL.seen[0]["format"] == "bestvideo+bestaudio/best"

    def test_explicit_quality_is_honoured(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path)
        d.download(URL, quality="360p")
        assert "height<=360" in FakeYDL.seen[0]["format"]

    def test_quality_can_come_from_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RCH_CLIP_QUALITY", "720p")
        d = stub(monkeypatch, tmp_path)
        d.download(URL)
        assert "height<=720" in FakeYDL.seen[0]["format"]

    def test_merges_to_mp4(self, downloader):
        assert self._run(downloader)["merge_output_format"] == "mp4"

    def test_retries_are_all_ten(self, downloader):
        opts = self._run(downloader)
        assert opts["retries"] == 10
        assert opts["fragment_retries"] == 10
        assert opts["extractor_retries"] == 10

    def test_ffmpeg_location_is_passed(self, downloader):
        assert self._run(downloader)["ffmpeg_location"] == "/fake/ffmpeg"

    def test_geo_bypass_and_certificate_flags(self, downloader):
        opts = self._run(downloader)
        assert opts["geo_bypass"] is True
        assert opts["nocheckcertificate"] is True

    def test_errors_are_not_ignored(self, downloader):
        """ignoreerrors=True would silently produce no file and no message."""
        assert self._run(downloader)["ignoreerrors"] is False

    def test_output_template_is_keyed_by_video_id(self, downloader):
        assert self._run(downloader)["outtmpl"].endswith(f"{VIDEO_ID}.%(ext)s")

    def test_user_agent_absent_by_default(self, downloader):
        assert "user_agent" not in self._run(downloader)

    def test_js_runtimes_are_passed_when_detected(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, js_runtimes={"node": {}})
        d.download(URL)
        assert FakeYDL.seen[0]["js_runtimes"] == {"node": {}}

    def test_user_agent_is_included_when_set(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, user_agent="UA/1.0")
        d.download(URL)
        assert FakeYDL.seen[0]["user_agent"] == "UA/1.0"


class TestJsRuntimeDetection:
    @pytest.mark.parametrize("runtime", ["node", "deno", "quickjs", "bun"])
    def test_first_available_runtime_wins(self, monkeypatch, runtime):
        monkeypatch.setattr(
            Y.shutil, "which",
            lambda name: f"/usr/bin/{name}" if name == runtime else None,
        )
        found = Y.YouTubeDownloader()._get_js_runtimes()
        assert list(found) == [runtime]

    def test_no_runtime_yields_empty(self, monkeypatch):
        monkeypatch.setattr(Y.shutil, "which", lambda name: None)
        assert Y.YouTubeDownloader()._get_js_runtimes() == {}


class TestChallengeSolverComponents:
    """yt-dlp needs the challenge solver script, not just a runtime.

    A JavaScript runtime is only half of it. Without the solver script itself
    yt-dlp prints "n challenge solving failed" and quietly drops some formats,
    which looks like a missing quality rather than a missing option. Both have
    to be requested together, and the request has to survive a retry.
    """

    def test_solver_script_is_requested(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path)
        d.download(URL)
        assert FakeYDL.seen[0]["remote_components"] == ["ejs:github"]

    def test_solver_script_is_requested_alongside_the_runtime(
        self, tmp_path, monkeypatch
    ):
        """The two are useless apart, so neither may be conditional on the
        other."""
        d = stub(monkeypatch, tmp_path, js_runtimes={"node": {}})
        d.download(URL)
        opts = FakeYDL.seen[0]
        assert opts["js_runtimes"] == {"node": {}}
        assert opts["remote_components"] == ["ejs:github"]

    def test_solver_script_survives_the_format_fallback(self, tmp_path,
                                                        monkeypatch):
        """The fallback copies opts, so it should carry the option over - but
        the copy is the fragile part, so it is asserted rather than assumed."""
        d = stub(monkeypatch, tmp_path, script=["boom", "boom", "ok"])
        d.download(URL)
        assert len(FakeYDL.seen) > 1, "the fallback ladder was never walked"
        for opts in FakeYDL.seen:
            assert opts["remote_components"] == ["ejs:github"]


# -- cookies ----------------------------------------------------------------


class TestCookiePrecedence:
    def test_cookie_file_wins_when_present_and_non_empty(
        self, tmp_path, monkeypatch
    ):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        d = stub(monkeypatch, tmp_path, cookies_file=cookie_file,
                 cookies_browser="chrome")
        d.download(URL)

        opts = FakeYDL.seen[0]
        assert opts["cookiefile"] == str(cookie_file.resolve())
        assert "cookiesfrombrowser" not in opts

    def test_empty_cookie_file_is_ignored(self, tmp_path, monkeypatch):
        empty = tmp_path / "cookies.txt"
        empty.write_text("", encoding="utf-8")
        d = stub(monkeypatch, tmp_path, cookies_file=empty,
                 cookies_browser="firefox")
        d.download(URL)

        opts = FakeYDL.seen[0]
        assert "cookiefile" not in opts
        assert opts["cookiesfrombrowser"] == ("firefox",)

    def test_content_from_env_wins_over_browser(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, cookies_content="# Netscape\ncookie",
                 cookies_browser="chrome")
        d.download(URL)

        opts = FakeYDL.seen[0]
        assert "cookiefile" in opts
        assert "cookiesfrombrowser" not in opts

    def test_placeholder_content_is_ignored(self, tmp_path, monkeypatch):
        """The .env.example ships PASTE...; sending that as a bearer would
        produce an opaque 401 instead of a clear "no cookies" notice."""
        d = stub(monkeypatch, tmp_path, cookies_content="PASTE cookies here",
                 cookies_browser="chrome")
        d.download(URL)

        opts = FakeYDL.seen[0]
        assert "cookiefile" not in opts
        assert opts["cookiesfrombrowser"] == ("chrome",)

    def test_no_cookies_at_all(self, downloader):
        downloader.download(URL)
        opts = FakeYDL.seen[0]
        assert "cookiefile" not in opts
        assert "cookiesfrombrowser" not in opts

    def test_temp_cookie_file_is_cleaned_up(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, cookies_content="# Netscape\ncookie")
        d.download(URL)
        assert list(tmp_path.glob("*.txt")) == [], "cookie file left behind"


# -- fallback ladder --------------------------------------------------------


class TestFallbackLadder:
    def test_first_attempt_succeeds_without_fallback(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, script=["ok"])
        d.download(URL)
        assert len(FakeYDL.seen) == 1

    def test_second_attempt_uses_player_clients(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, script=["boom", "ok"])
        d.download(URL)

        assert len(FakeYDL.seen) == 2
        fallback = FakeYDL.seen[1]
        assert fallback["extractor_args"]["youtube"]["player_client"] == [
            "tv", "web", "android", "ios"
        ]
        assert fallback["format"] == "bestvideo+bestaudio/best[ext=mp4]/best"

    def test_third_attempt_is_the_general_fallback(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, script=["boom", "boom", "ok"])
        d.download(URL)
        assert len(FakeYDL.seen) == 3
        assert FakeYDL.seen[2]["format"] == "best/bestvideo*+bestaudio"

    def test_all_attempts_failing_raises(self, tmp_path, monkeypatch):
        d = stub(monkeypatch, tmp_path, script=["boom", "boom", "boom"])
        with pytest.raises(Exception, match="Failed to download"):
            d.download(URL)


# -- return contract --------------------------------------------------------


class TestReturnContract:
    def test_returns_path_title_duration(self, downloader):
        path, title, duration = downloader.download(URL)
        assert title == "Judul Video"
        assert duration == 212
        assert path.exists()

    def test_missing_title_falls_back_to_na(self, downloader, monkeypatch):
        class NoTitle(FakeYDL):
            def extract_info(self, url, download=True):
                write_output(self.opts)
                return {}

        monkeypatch.setattr(Y.yt_dlp, "YoutubeDL", NoTitle)
        _, title, _ = downloader.download(URL)
        assert title == "N/A"

    def test_no_file_on_disk_raises(self, downloader, monkeypatch):
        class NoFile(FakeYDL):
            def extract_info(self, url, download=True):
                return {"title": "T", "duration": 1}

        monkeypatch.setattr(Y.yt_dlp, "YoutubeDL", NoFile)
        with pytest.raises(FileNotFoundError, match="Failed to download"):
            downloader.download(URL)

    def test_temp_dir_is_created(self, tmp_path, monkeypatch):
        target = tmp_path / "belum-ada"
        d = stub(monkeypatch, target)
        d.download(URL)
        assert target.is_dir()