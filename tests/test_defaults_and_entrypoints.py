"""Default-dependency paths and module entrypoints.

Every other test injects its network boundary. These cover the opposite side:
the *production* call sites where the injection is omitted and the function
falls back to importing ``rch.core.http`` itself. That fallback is what the CLI
and the web GUI actually run, so a regression there would be invisible to an
injection-only suite.

The ``python -m`` entrypoints are covered by subprocess smoke tests, which is
the only way to exercise ``if __name__ == "__main__"`` guards.
"""
from __future__ import annotations

import runpy
import subprocess
import sys
import types
from pathlib import Path

import pytest

from rch.youtube.thumbnail import download_thumbnail

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL1234567890"
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VIDEO_ID = "dQw4w9WgXcQ"


def _response(**attrs):
    return types.SimpleNamespace(**attrs)


# ---------------------------------------------------------------------------
# thumbnail: default http_get fallback
# ---------------------------------------------------------------------------


class TestThumbnailDefaultHttpGet:
    def test_falls_back_to_core_http_and_writes_bytes(self, tmp_path, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url
            return _response(content=b"\xff\xd8\xff-bytes")

        monkeypatch.setattr("rch.core.http.http_get", fake_get)

        result = download_thumbnail(VIDEO_URL, output_dir=str(tmp_path))

        assert result["status"] is True
        assert Path(result["result"]["path"]).exists()

    def test_default_call_targets_the_canonical_thumbnail_url(self, tmp_path, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url
            return _response(content=b"IMG")

        monkeypatch.setattr("rch.core.http.http_get", fake_get)

        download_thumbnail(VIDEO_URL, output_dir=str(tmp_path), size="hqdefault")

        assert seen["url"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"

    def test_response_content_is_written_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setattr("rch.core.http.http_get",
                            lambda url, **kw: _response(content=b"RAW-BYTES"))

        result = download_thumbnail(VIDEO_URL, output_dir=str(tmp_path))

        assert Path(result["result"]["path"]).read_bytes() == b"RAW-BYTES"

    def test_transport_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        def boom(url, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr("rch.core.http.http_get", boom)

        result = download_thumbnail(VIDEO_URL, output_dir=str(tmp_path))

        assert result["status"] is False

    def test_injected_get_still_takes_precedence(self, tmp_path, monkeypatch):
        """An explicit injection must not be overridden by the default."""
        monkeypatch.setattr("rch.core.http.http_get",
                            lambda url, **kw: pytest.fail("default http_get must not be used"))
        monkeypatch.setattr("rch.youtube.video.get_title", lambda video_id: "Fixed Title")

        result = download_thumbnail(VIDEO_URL, output_dir=str(tmp_path),
                                    http_get=lambda url: b"INJECTED")

        assert result["status"] is True
        assert Path(result["result"]["path"]).read_bytes() == b"INJECTED"

    def test_title_falls_back_to_video_id_when_oembed_fails(self, tmp_path, monkeypatch):
        """With no injection the title is fetched from oEmbed, degrading to the id."""
        monkeypatch.setattr("rch.core.http.http_get",
                            lambda url, **kw: (_ for _ in ()).throw(RuntimeError("offline")))

        result = download_thumbnail(VIDEO_URL, output_dir=str(tmp_path),
                                    http_get=lambda url: b"INJECTED")

        assert result["status"] is True
        assert result["result"]["title"] == VIDEO_ID


# ---------------------------------------------------------------------------
# playlist: default fetch_html fallback
# ---------------------------------------------------------------------------


class TestPlaylistDefaultFetchHtml:
    def _html(self, body="{}"):
        return f'<script>var ytInitialData = {body};</script>'

    def test_falls_back_to_core_http(self, monkeypatch):
        from rch.youtube.playlist import scrape

        seen = {}
        html = self._html()

        def fake_get(url, **kwargs):
            seen["url"] = url
            return _response(text=html)

        monkeypatch.setattr("rch.core.http.http_get", fake_get)

        scrape(PLAYLIST_URL)

        assert seen["url"] == "https://www.youtube.com/playlist?list=PL1234567890"

    def test_default_call_uses_the_canonical_playlist_url(self, monkeypatch):
        from rch.youtube.playlist import scrape

        seen = {}

        def fake_get(url, **kwargs):
            seen.setdefault("urls", []).append(url)
            return _response(text=self._html())

        monkeypatch.setattr("rch.core.http.http_get", fake_get)

        scrape("https://www.youtube.com/watch?v=x&list=PLABC")

        assert seen["urls"] == ["https://www.youtube.com/playlist?list=PLABC"]

    def test_transport_failure_becomes_a_failure_envelope(self, monkeypatch):
        from rch.youtube.playlist import scrape

        def boom(url, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr("rch.core.http.http_get", boom)

        result = scrape(PLAYLIST_URL)

        assert result["status"] is False

    def test_missing_ytinitialdata_is_reported(self, monkeypatch):
        from rch.youtube.playlist import scrape

        monkeypatch.setattr("rch.core.http.http_get",
                            lambda url, **kw: _response(text="<html>no data</html>"))

        result = scrape(PLAYLIST_URL)

        assert result["status"] is False
        assert "ytInitialData" in result["message"]

    def test_injected_fetcher_takes_precedence(self, monkeypatch):
        from rch.youtube.playlist import scrape

        monkeypatch.setattr("rch.core.http.http_get",
                            lambda url, **kw: pytest.fail("default http_get must not be used"))

        result = scrape(PLAYLIST_URL, fetch_html=lambda url: self._html())

        assert result["status"] is True


# ---------------------------------------------------------------------------
# Module entrypoints
# ---------------------------------------------------------------------------


def _run_module(*args):
    return subprocess.run(
        [sys.executable, "-m", *args],
        capture_output=True, text=True, timeout=60,
    )


class TestConsoleEntrypoint:
    """Subprocess smoke tests: these verify the real ``python -m`` wiring."""

    def test_help_runs(self):
        result = _run_module("rch", "--help")

        assert result.returncode == 0, result.stderr
        assert "Usage:" in result.stdout

    def test_version_prints_the_package_version(self):
        from rch import __version__

        result = _run_module("rch", "--version")

        assert result.returncode == 0, result.stderr
        assert __version__ in result.stdout

    def test_version_does_not_require_an_installed_distribution(self):
        """``--version`` must work from a source checkout, not only when pip-installed."""
        result = _run_module("rch", "--version")

        assert "not installed" not in (result.stdout + result.stderr)

    def test_unknown_subcommand_exits_nonzero(self):
        assert _run_module("rch", "definitely-not-a-command").returncode != 0


class TestModuleMainGuards:
    """``runpy`` is the only way to execute ``if __name__ == "__main__"`` blocks."""

    def test_rch_main_delegates_to_cli_main(self, monkeypatch):
        called = []
        monkeypatch.setattr("rch.cli.main", lambda: called.append("main"))

        runpy.run_module("rch.__main__", run_name="__main__")

        assert called == ["main"]

    def test_rch_web_main_delegates_to_run_server(self, monkeypatch):
        called = []
        monkeypatch.setattr("rch.web.server.run_server", lambda *a, **k: called.append((a, k)))

        runpy.run_module("rch.web.__main__", run_name="__main__")

        assert called == [((), {})]

    def test_cli_module_guard_runs_the_cli(self, monkeypatch, capsys):
        """``python -m rch.cli`` executes the guard at the bottom of the module.

        ``runpy`` re-executes the module body, so the namespace cannot be
        patched from outside — the guard is driven through a controlled argv
        instead and observed via its output and exit code.
        """
        from rch import __version__

        monkeypatch.setattr(sys, "argv", ["rch", "--version"])

        with pytest.raises(SystemExit) as exc:
            runpy.run_module("rch.cli", run_name="__main__")

        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out