"""Tests for rch.youtube.video subprocess/fetcher boundaries.

These cover the default (non-injected) code paths: the real ``yt-dlp`` runner,
the ``yt-dlp -U`` updater, and the default HTTP title fetcher. ``subprocess.run``
and ``requests.get`` are monkeypatched so no process is spawned and no network
traffic occurs.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from rch.youtube import video as video_mod
from rch.youtube.video import default_run_ytdlp, ensure_ytdlp_updated, get_title

VIDEO_ID = "dQw4w9WgXcQ"


def _proc(*, stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestDefaultRunYtdlp:
    def test_returns_stdout_on_success(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _proc(stdout="done\n")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert default_run_ytdlp(["--dump-json", "URL"]) == "done\n"
        assert captured["cmd"][0] == "yt-dlp"
        assert captured["cmd"][-1] == "URL"

    def test_applies_config_mitigation_flags(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _proc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        default_run_ytdlp(["-f", "best"])

        assert "--sleep-requests" in captured["cmd"]
        assert "--user-agent" in captured["cmd"]

    def test_no_sleep_omits_sleep_flags(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _proc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        default_run_ytdlp(["-f", "best"], no_sleep=True)

        assert "--sleep-requests" not in captured["cmd"]
        assert "--sleep-interval" not in captured["cmd"]

    def test_cookies_flag_added_when_requested(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _proc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        default_run_ytdlp(["-f", "best"], cookies="firefox")

        assert "--cookies-from-browser" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--cookies-from-browser") + 1] == "firefox"

    def test_timeout_passed_to_subprocess(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _proc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        default_run_ytdlp(["-f", "best"], timeout=42)

        assert captured["kwargs"]["timeout"] == 42

    def test_timeout_omitted_when_zero(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return _proc(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        default_run_ytdlp(["-f", "best"])

        assert "timeout" not in captured["kwargs"]

    def test_streams_stderr_lines_to_callback(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: _proc(stderr="[download] 50%\n\n[download] 100%\n"),
        )
        lines = []
        default_run_ytdlp(["-f", "best"], on_line=lines.append)

        assert lines == ["[download] 50%", "[download] 100%"]

    def test_no_callback_means_no_streaming_error(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(stderr="noise\n"))
        assert default_run_ytdlp(["-f", "best"]) == ""

    def test_nonzero_exit_raises_with_stderr(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: _proc(stderr="ERROR: Video unavailable", returncode=1),
        )
        with pytest.raises(RuntimeError, match="Video unavailable"):
            default_run_ytdlp(["-f", "best"])

    def test_nonzero_exit_without_stderr_uses_returncode(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(returncode=2))
        with pytest.raises(RuntimeError, match="yt-dlp exited with 2"):
            default_run_ytdlp(["-f", "best"])

    def test_missing_binary_raises_actionable_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("yt-dlp")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="yt-dlp tidak ditemukan"):
            default_run_ytdlp(["-f", "best"])

    def test_none_stdout_becomes_empty_string(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(stdout=None))
        assert default_run_ytdlp(["-f", "best"]) == ""


class TestEnsureYtdlpUpdated:
    def test_invokes_update_flag(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _proc()

        monkeypatch.setattr(subprocess, "run", fake_run)
        ensure_ytdlp_updated()

        assert captured["cmd"] == ["yt-dlp", "-U"]

    def test_swallows_update_failure(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ensure_ytdlp_updated()  # must not raise

    def test_swallows_timeout(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(subprocess, "run", fake_run)
        ensure_ytdlp_updated()  # must not raise


class TestGetTitleDefaultFetcher:
    def test_uses_injected_http_get_when_none_given(self, monkeypatch):
        # No http_get passed -> module falls back to rch.core.http.http_get.
        import rch.core.http as http_mod

        captured = {}

        class FakeResponse:
            text = '{"title": "Default Path Title"}'

        def fake_http_get(url, **kwargs):
            captured["url"] = url
            captured["timeout"] = kwargs.get("timeout")
            return FakeResponse()

        monkeypatch.setattr(http_mod, "http_get", fake_http_get)
        assert get_title(VIDEO_ID) == "Default Path Title"
        assert VIDEO_ID in captured["url"]
        assert captured["timeout"] == 5

    def test_default_fetcher_network_error_falls_back_to_id(self, monkeypatch):
        import rch.core.http as http_mod

        def fake_http_get(url, **kwargs):
            raise ConnectionError("offline")

        monkeypatch.setattr(http_mod, "http_get", fake_http_get)
        assert get_title(VIDEO_ID) == VIDEO_ID


class TestDefaultRunnerWiring:
    def test_download_once_uses_module_runner_when_none_injected(self, tmp_path, monkeypatch):
        # Exercise the `run_ytdlp if run_ytdlp is not None else default_run_ytdlp`
        # branch by patching the module-level default runner.
        calls = []

        def fake_runner(args, **kwargs):
            calls.append((args, kwargs))
            template = args[args.index("-o") + 1]
            path = tmp_path / template.split(str(tmp_path))[-1].lstrip("/\\").replace(
                "%(ext)s", "mp4"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 5)

        monkeypatch.setattr(video_mod, "default_run_ytdlp", fake_runner)

        result = video_mod.download_once(
            "https://youtu.be/dQw4w9WgXcQ",
            {"outputDir": str(tmp_path)},
            fetch_title=lambda _id: "Title Here",
        )
        assert result["status"] is True
        assert len(calls) == 1
        assert result["result"]["sizeBytes"] == 5

    def test_title_fetcher_exception_falls_back_to_video_id(self, tmp_path):
        # The title lookup is best-effort: a raising fetcher must not break
        # the download, and the video id becomes the filename stem.
        def fake_runner(args, **kwargs):
            template = args[args.index("-o") + 1]
            path = tmp_path / template.split(str(tmp_path))[-1].lstrip("/\\").replace(
                "%(ext)s", "mp4"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"z" * 7)

        def boom(_video_id):
            raise RuntimeError("oembed down")

        result = video_mod.download_once(
            "https://youtu.be/dQw4w9WgXcQ",
            {"outputDir": str(tmp_path)},
            run_ytdlp=fake_runner,
            fetch_title=boom,
        )
        assert result["status"] is True
        assert result["result"]["title"] == "dQw4w9WgXcQ"

    def test_download_passes_no_sleep_and_cookies_through(self, tmp_path):
        seen = {}

        def fake_runner(args, **kwargs):
            seen.update(kwargs)
            template = args[args.index("-o") + 1]
            path = tmp_path / template.split(str(tmp_path))[-1].lstrip("/\\").replace(
                "%(ext)s", "mp4"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"y" * 3)

        result = video_mod.download(
            "https://youtu.be/dQw4w9WgXcQ",
            {"outputDir": str(tmp_path), "noSleep": True, "cookies": "chrome"},
            run_ytdlp=fake_runner,
            fetch_title=lambda _id: "T",
            sleep=lambda _s: None,
        )
        assert result["status"] is True
        assert seen["no_sleep"] is True
        assert seen["cookies"] == "chrome"

    def test_download_defaults_sleep_to_time_module(self, tmp_path, monkeypatch):
        # Non-retryable failure: the real `time.sleep` default is resolved but
        # never invoked, so the global time module is left untouched.
        def fake_runner(args, **kwargs):
            raise ValueError("Invalid YouTube video URL.")

        result = video_mod.download(
            "https://example.com/not-a-video",
            {"outputDir": str(tmp_path)},
            run_ytdlp=fake_runner,
            fetch_title=lambda _id: "T",
        )
        assert result["status"] is False

    def test_failure_envelope_falls_back_to_generic_message(self, tmp_path, monkeypatch):
        # Zero retries with a transient error exhausts the loop; the message is
        # still surfaced from the last attempt.
        def fake_runner(args, **kwargs):
            raise RuntimeError("HTTP Error 403: Forbidden")

        result = video_mod.download(
            "https://youtu.be/dQw4w9WgXcQ",
            {"outputDir": str(tmp_path), "retries": 0},
            run_ytdlp=fake_runner,
            fetch_title=lambda _id: "T",
            sleep=lambda _s: None,
        )
        assert result["status"] is False
        assert "403" in result["message"]