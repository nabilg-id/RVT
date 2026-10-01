"""Command-surface tests for rch.cli — every subcommand, via click's CliRunner.

Every engine call is patched at its defining module (``rch.youtube.*``) because
``cli.py`` imports those modules lazily *inside* each callback, so attribute
lookup happens at call time and monkeypatching works cleanly.

The channel commands additionally get structural guards: the legacy CLI has a
positional thumbnail-size argument alongside ``--out``, and a name collision
between the two silently swallows the output directory.
"""
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from rch import cli as cli_mod
from rch.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_cwd(monkeypatch, tmp_path):
    """Run every CLI test from a scratch directory.

    Commands that omit ``--out`` default to ``./downloads`` and write
    ``report.txt`` / ``history.log`` there, so without this the suite would
    litter the developer's working tree and make runs order-dependent.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _clean_cookie_env(monkeypatch):
    monkeypatch.delenv("RCH_COOKIES", raising=False)


class _Recorder:
    """Captures the arguments a patched engine function was called with.

    Pass ``func=`` to substitute a custom implementation (e.g. one that emits
    progress events) while still recording the call.
    """

    def __init__(self, result=None, func=None):
        self.calls = []
        self.result = result if result is not None else {
            "status": True,
            "result": {"total": 1, "success": 1, "failed": 0, "items": []},
        }
        self.func = func

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.func is not None:
            return self.func(*args, **kwargs)
        return self.result

    @property
    def last_options(self):
        args, kwargs = self.calls[-1]
        return kwargs.get("options", args[1] if len(args) > 1 else {})


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


class TestGroup:
    def test_no_args_prints_help(self, runner):
        result = runner.invoke(cli, [])

        assert result.exit_code == 0
        assert "Usage:" in result.output

    def test_version_flag(self, runner):
        from rch import __version__

        result = runner.invoke(cli, ["--version"])

        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help_lists_every_legacy_subcommand(self, runner):
        result = runner.invoke(cli, ["--help"])

        for name in ("thumbnail", "channel", "list", "info", "video", "download",
                     "channel-full", "channel-video", "channel-info",
                     "web", "gui", "serve"):
            assert name in result.output

    def test_unknown_subcommand_exits_nonzero(self, runner):
        assert runner.invoke(cli, ["nope"]).exit_code != 0

    def test_main_delegates_to_cli(self, monkeypatch):
        called = {}
        monkeypatch.setattr(cli_mod, "cli", lambda **kw: called.setdefault("kw", kw))

        cli_mod.main()

        assert "kw" in called


# ---------------------------------------------------------------------------
# Structural guards: no duplicate click parameter names
# ---------------------------------------------------------------------------


class TestNoDuplicateParameters:
    @pytest.mark.parametrize(
        "command_name",
        ["thumbnail", "channel", "list_cmd", "info", "video", "download",
         "channel_full_cmd", "channel_video_cmd", "channel_info_cmd"],
    )
    def test_parameter_names_are_unique(self, command_name):
        params = getattr(cli_mod, command_name).params
        names = [p.name for p in params]

        assert len(names) == len(set(names)), f"{command_name} has duplicate param names"

    @pytest.mark.parametrize(
        "command_name", ["channel_full_cmd", "channel_info_cmd", "channel_video_cmd"]
    )
    def test_out_is_an_option_not_a_positional(self, command_name):
        params = getattr(cli_mod, command_name).params
        out_params = [p for p in params if p.name == "out"]

        assert len(out_params) == 1
        assert getattr(out_params[0], "opts", None) == ["--out"]


# ---------------------------------------------------------------------------
# thumbnail
# ---------------------------------------------------------------------------


class TestThumbnailCommand:
    def test_success_prints_path(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {
            "id": "abc12345678", "title": "Judul", "path": "/tmp/a.jpg",
        }})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        result = runner.invoke(cli, ["thumbnail", "https://youtu.be/vid"])

        assert result.exit_code == 0
        assert "[OK] Judul" in result.output
        assert "Disimpan:" in result.output

    def test_default_size_is_maxresdefault(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "t", "path": "/tmp/a.jpg"}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        runner.invoke(cli, ["thumbnail", "https://youtu.be/vid"])

        assert rec.calls[0][1]["size"] == "maxresdefault"

    def test_size_positional_is_forwarded(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "t", "path": "/tmp/a.jpg"}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        runner.invoke(cli, ["thumbnail", "https://youtu.be/vid", "hqdefault"])

        assert rec.calls[0][1]["size"] == "hqdefault"

    def test_out_option_is_forwarded(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "t", "path": "/tmp/a.jpg"}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        runner.invoke(cli, ["thumbnail", "https://youtu.be/vid", "--out", "/data"])

        assert rec.calls[0][1]["output_dir"] == "/data"

    def test_default_output_dir_is_timestamped(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "t", "path": "/tmp/a.jpg"}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        runner.invoke(cli, ["thumbnail", "https://youtu.be/vid"])

        out_dir = rec.calls[0][1]["output_dir"]
        assert out_dir.startswith("./downloads/")

    def test_zip_path_is_echoed_absolute(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {
            "title": "t", "path": "/tmp/a.jpg", "zipPath": "/tmp/a.zip",
        }})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", rec)

        result = runner.invoke(cli, ["thumbnail", "https://youtu.be/vid"])

        assert "ZIP:" in result.output

    def test_failure_exits_with_code_1(self, runner, monkeypatch):
        monkeypatch.setattr(
            "rch.youtube.thumbnail.download_thumbnail",
            _Recorder({"status": False, "message": "gagal"}),
        )

        result = runner.invoke(cli, ["thumbnail", "https://youtu.be/vid"])

        assert result.exit_code == 1

    def test_missing_url_is_a_usage_error(self, runner):
        assert runner.invoke(cli, ["thumbnail"]).exit_code == 2


# ---------------------------------------------------------------------------
# channel (thumbnails only)
# ---------------------------------------------------------------------------


class TestChannelCommand:
    @pytest.fixture(autouse=True)
    def _stub_channel_ids(self, monkeypatch):
        monkeypatch.setattr(
            "rch.youtube.metadata.get_channel_ids", lambda url: ["a", "b", "c"]
        )

    def test_lists_ids_before_downloading(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 3, "success": 3, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        result = runner.invoke(cli, ["channel", "https://x/@ch"])

        assert result.exit_code == 0
        assert "Mengambil daftar video dari channel..." in result.output
        assert "Ditemukan 3 video" in result.output

    def test_builds_youtu_be_urls_from_ids(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 3, "success": 3, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        runner.invoke(cli, ["channel", "https://x/@ch"])

        assert rec.calls[0][0][0] == [
            "https://youtu.be/a", "https://youtu.be/b", "https://youtu.be/c",
        ]

    def test_zip_is_always_requested(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 3, "success": 3, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        runner.invoke(cli, ["channel", "https://x/@ch"])

        assert rec.calls[0][1]["zip"] is True
        assert rec.calls[0][1]["zip_name"].startswith("channel-thumbnails-")

    def test_limit_truncates_ids(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 2, "success": 2, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        runner.invoke(cli, ["channel", "https://x/@ch", "--limit", "2"])

        assert rec.calls[0][0][0] == ["https://youtu.be/a", "https://youtu.be/b"]

    def test_size_positional_is_forwarded(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 3, "success": 3, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        runner.invoke(cli, ["channel", "https://x/@ch", "sddefault"])

        assert rec.calls[0][1]["size"] == "sddefault"

    def test_success_prints_summary(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 5, "success": 4, "failed": 1}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        result = runner.invoke(cli, ["channel", "https://x/@ch"])

        assert result.exit_code == 0
        assert "Total: 5" in result.output
        assert "Sukses: 4" in result.output
        assert "Gagal: 1" in result.output

    def test_zip_path_is_echoed(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"total": 1, "success": 1, "failed": 0,
                                                     "zipPath": "/tmp/c.zip"}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        assert "ZIP:" in runner.invoke(cli, ["channel", "https://x/@ch"]).output

    def test_writes_report_and_history(self, runner, monkeypatch, tmp_path):
        rec = _Recorder({"status": True, "result": {"total": 1, "success": 1, "failed": 0}})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        runner.invoke(cli, ["channel", "https://x/@ch", "--out", str(tmp_path)])

        assert (tmp_path / "report.txt").exists()
        assert (tmp_path / "history.log").exists()

    def test_lists_failed_urls(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {
            "total": 2, "success": 1, "failed": 1,
            "items": [
                {"status": True, "url": "https://youtu.be/a"},
                {"status": False, "url": "https://youtu.be/b", "message": "private"},
            ],
        }})
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnails", rec)

        result = runner.invoke(cli, ["channel", "https://x/@ch"])

        assert "https://youtu.be/b" in result.output

    def test_id_lookup_failure_exits_with_code_1(self, runner, monkeypatch):
        def _boom(url):
            raise RuntimeError("yt-dlp tidak terpasang")

        monkeypatch.setattr("rch.youtube.metadata.get_channel_ids", _boom)

        result = runner.invoke(cli, ["channel", "https://x/@ch"])

        assert result.exit_code == 1
        assert "yt-dlp tidak terpasang" in result.output

    def test_failure_exits_with_code_1(self, runner, monkeypatch):
        monkeypatch.setattr(
            "rch.youtube.thumbnail.download_thumbnails",
            _Recorder({"status": False, "message": "gagal"}),
        )

        assert runner.invoke(cli, ["channel", "https://x/@ch"]).exit_code == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_prints_ids_one_per_line(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.list_ids", lambda url, opts: ["a", "b", "c"])

        result = runner.invoke(cli, ["list", "https://x/@ch"])

        assert result.output.strip().splitlines() == ["a", "b", "c"]

    def test_limit_is_forwarded(self, runner, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.channel.list_ids",
                            lambda url, opts: seen.setdefault("opts", opts) and [] or ["a"])

        runner.invoke(cli, ["list", "https://x/@ch", "--limit", "3"])

        assert seen["opts"] == {"limit": 3}

    def test_writes_to_out_file(self, runner, monkeypatch, tmp_path):
        monkeypatch.setattr("rch.youtube.channel.list_ids", lambda url, opts: ["a", "b"])
        out = tmp_path / "ids.txt"

        result = runner.invoke(cli, ["list", "https://x/@ch", str(out)])

        assert out.read_text(encoding="utf-8") == "a\nb"
        assert "Ditulis:" in result.output

    def test_empty_result_prints_nothing(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.channel.list_ids", lambda url, opts: [])

        result = runner.invoke(cli, ["list", "https://x/@ch"])

        assert result.exit_code == 0
        assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------


class TestInfoCommand:
    def test_prints_tab_separated_rows(self, runner, monkeypatch):
        monkeypatch.setattr(
            "rch.youtube.metadata.get_video_info_batch",
            lambda ids: {"vid1": {"title": "Judul", "duration": 120}},
        )

        result = runner.invoke(cli, ["info", "vid1", "vid2"])

        assert "vid1\tJudul\t120" in result.output

    def test_all_ids_are_forwarded(self, runner, monkeypatch):
        seen = {}
        monkeypatch.setattr("rch.youtube.metadata.get_video_info_batch",
                            lambda ids: seen.setdefault("ids", ids) and {} or {})

        runner.invoke(cli, ["info", "vid1", "vid2", "vid3"])

        assert seen["ids"] == ["vid1", "vid2", "vid3"]

    def test_csv_export(self, runner, monkeypatch, tmp_path):
        monkeypatch.setattr("rch.youtube.metadata.get_video_info_batch",
                            lambda ids: {"vid1": {"title": "T"}})
        csv_file = tmp_path / "m.csv"

        result = runner.invoke(cli, ["info", "vid1", "--csv", str(csv_file)])

        assert "CSV:" in result.output
        assert "T" in csv_file.read_text(encoding="utf-8")

    def test_json_export(self, runner, monkeypatch, tmp_path):
        monkeypatch.setattr("rch.youtube.metadata.get_video_info_batch",
                            lambda ids: {"vid1": {"title": "T"}})
        json_file = tmp_path / "m.json"

        runner.invoke(cli, ["info", "vid1", "--json", str(json_file)])

        assert "T" in json_file.read_text(encoding="utf-8")

    def test_no_ids_is_a_usage_error(self, runner):
        assert runner.invoke(cli, ["info"]).exit_code == 2

    def test_missing_fields_render_as_empty(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.metadata.get_video_info_batch",
                            lambda ids: {"vid1": {}})

        assert "vid1\t\t" in runner.invoke(cli, ["info", "vid1"]).output


# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------


class TestVideoCommand:
    def test_success_prints_title_and_path(self, runner, monkeypatch):
        monkeypatch.setattr(
            "rch.youtube.video.download",
            _Recorder({"status": True, "result": {"title": "Judul", "path": "/tmp/v.mp4"}}),
        )

        result = runner.invoke(cli, ["video", "https://youtu.be/vid"])

        assert result.exit_code == 0
        assert "[OK] Judul -> /tmp/v.mp4" in result.output

    def test_default_format_is_mp4(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "T", "path": "/tmp/v"}})
        monkeypatch.setattr("rch.youtube.video.download", rec)

        runner.invoke(cli, ["video", "https://youtu.be/vid"])

        assert rec.last_options["format"] == "mp4"

    def test_mp3_flag_selects_mp3(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "T", "path": "/tmp/v"}})
        monkeypatch.setattr("rch.youtube.video.download", rec)

        runner.invoke(cli, ["video", "https://youtu.be/vid", "--mp3"])

        assert rec.last_options["format"] == "mp3"

    def test_name_argument_becomes_filename(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "T", "path": "/tmp/v"}})
        monkeypatch.setattr("rch.youtube.video.download", rec)

        runner.invoke(cli, ["video", "https://youtu.be/vid", "myvideo"])

        assert rec.last_options["filename"] == "myvideo"

    def test_subtitle_options_are_forwarded(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "T", "path": "/tmp/v"}})
        monkeypatch.setattr("rch.youtube.video.download", rec)

        runner.invoke(cli, ["video", "https://youtu.be/vid", "--subtitles", "--sub-lang", "en"])

        assert rec.last_options["subtitles"] is True
        assert rec.last_options["subLang"] == "en"

    def test_cookies_are_exported_to_environment(self, runner, monkeypatch):
        rec = _Recorder({"status": True, "result": {"title": "T", "path": "/tmp/v"}})
        monkeypatch.setattr("rch.youtube.video.download", rec)

        runner.invoke(cli, ["video", "https://youtu.be/vid", "--cookies", "chrome"])

        assert os.environ["RCH_COOKIES"] == "chrome"

    def test_failure_exits_with_code_1(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.video.download",
                            _Recorder({"status": False, "message": "gagal"}))

        assert runner.invoke(cli, ["video", "https://youtu.be/vid"]).exit_code == 1


# ---------------------------------------------------------------------------
# download (video + thumbnail)
# ---------------------------------------------------------------------------


class TestDownloadCommand:
    @pytest.fixture(autouse=True)
    def _patches(self, monkeypatch):
        self.video = _Recorder({"status": True, "result": {"path": "/tmp/v.mp4"}})
        self.thumb = _Recorder({"status": True, "result": {"path": "/tmp/t.jpg"}})
        monkeypatch.setattr("rch.youtube.video.download", self.video)
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail", self.thumb)

    def test_success_prints_both_paths(self, runner):
        result = runner.invoke(cli, ["download", "https://youtu.be/vid"])

        assert result.exit_code == 0
        assert "[OK] Video: /tmp/v.mp4" in result.output
        assert "[OK] Thumbnail: /tmp/t.jpg" in result.output

    def test_output_dir_is_forwarded_to_both(self, runner, tmp_path):
        runner.invoke(cli, ["download", "https://youtu.be/vid", "", str(tmp_path)])

        assert self.video.last_options["outputDir"] == str(tmp_path)
        assert self.thumb.calls[0][1]["output_dir"] == str(tmp_path)

    def test_video_failure_aborts_before_thumbnail(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.video.download",
                            _Recorder({"status": False, "message": "video gagal"}))

        result = runner.invoke(cli, ["download", "https://youtu.be/vid"])

        assert result.exit_code == 1
        assert self.thumb.calls == []

    def test_thumbnail_failure_is_non_fatal(self, runner, monkeypatch):
        monkeypatch.setattr("rch.youtube.thumbnail.download_thumbnail",
                            _Recorder({"status": False, "message": "thumb gagal"}))

        result = runner.invoke(cli, ["download", "https://youtu.be/vid"])

        assert result.exit_code == 0
        assert "[!] Thumbnail: thumb gagal" in result.output

    def test_zip_flag_forwarded_to_thumbnail(self, runner):
        runner.invoke(cli, ["download", "https://youtu.be/vid", "--zip"])

        assert self.thumb.calls[0][1]["zip"] is True


# ---------------------------------------------------------------------------
# channel-full / channel-video
# ---------------------------------------------------------------------------


class TestChannelFullCommand:
    @pytest.fixture
    def engine(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr("rch.youtube.channel.channel_full", rec)
        return rec

    def test_success_writes_report_and_history(self, runner, engine, tmp_path):
        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / "report.txt").exists()
        assert (tmp_path / "history.log").exists()

    def test_out_option_reaches_the_engine(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])

        assert engine.last_options["outputDir"] == str(tmp_path)

    def test_second_positional_is_the_size_not_an_output_dir(self, runner, engine):
        """Legacy contract: ``channel-full <url> [ukuran] [--out <folder>]``."""
        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "hqdefault"])

        assert result.exit_code == 0
        assert engine.last_options["size"] == "hqdefault"
        assert engine.last_options["outputDir"] == cli_mod.DEFAULT_OUT

    def test_out_option_reaches_report_writer(self, runner, engine, tmp_path):
        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])

        assert f"Report: {os.path.abspath(tmp_path / 'report.txt')}" in result.output

    def test_size_positional_reaches_the_engine(self, runner, engine):
        runner.invoke(cli, ["channel-full", "https://x/@ch", "maxresdefault"])

        assert engine.last_options["size"] == "maxresdefault"

    def test_default_output_dir_is_used_when_unspecified(self, runner, engine):
        runner.invoke(cli, ["channel-full", "https://x/@ch"])

        assert engine.last_options["outputDir"] == cli_mod.DEFAULT_OUT

    def test_shared_flags_are_forwarded(self, runner, engine, tmp_path):
        runner.invoke(cli, [
            "channel-full", "https://x/@ch", "--out", str(tmp_path),
            "--limit", "3", "--quality", "1080p", "--concurrency", "5",
            "--min-duration", "60", "--max-duration", "600", "--after", "20260101",
            "--shorts", "--subtitles", "--resume", "--sub-lang", "en",
        ])

        opts = engine.last_options
        assert opts["limit"] == 3
        assert opts["quality"] == "1080p"
        assert opts["concurrency"] == 5
        assert opts["minDuration"] == 60
        assert opts["maxDuration"] == 600
        assert opts["after"] == "20260101"
        assert opts["shorts"] is True
        assert opts["subtitles"] is True
        assert opts["resume"] is True
        assert opts["subLang"] == "en"

    def test_cookies_are_exported_to_environment(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-full", "https://x/@ch", "--cookies", "firefox"])

        assert os.environ["RCH_COOKIES"] == "firefox"

    def test_emitter_is_supplied_to_engine(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])

        assert engine.calls[-1][1]["emitter"] is not None

    def test_progress_events_are_rendered(self, runner, engine, tmp_path):
        def emit(link, options, emitter=None):
            emitter.emit("phase", {"phase": "list", "msg": "Mengambil daftar video"})
            emitter.emit("progress", {"done": 1, "total": 2, "label": "Judul"})
            emitter.emit("video:done", {"id": "vid1", "title": "Judul", "ok": True})
            emitter.emit("video:done", {"id": "vid2", "title": "X", "ok": False,
                                        "error": "403"})
            return {"status": True, "result": {"total": 2, "success": 1, "failed": 1}}

        engine.func = emit
        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])
        rendered = result.output + result.stderr

        assert result.exit_code == 0
        assert "[*] Mengambil daftar video" in rendered
        assert "(1/2)" in rendered
        assert "[OK] vid1 Judul" in rendered
        assert "[!!] vid2 403" in rendered

    def test_engine_event_vocabulary_is_actually_subscribed(self, runner, engine, tmp_path):
        """The renderer must listen to the events channel.py actually emits."""
        seen = []

        def emit(link, options, emitter=None):
            for event in ("progress", "phase", "start", "video:start", "video:done",
                          "complete"):
                payload = ({"done": 1, "total": 1, "label": "L"} if event == "progress"
                           else {"phase": "download", "msg": "M"} if event == "phase"
                           else {"id": "vid1", "title": "T", "ok": True})
                emitter.emit(event, payload)
                seen.append(event)
            return {"status": True, "result": {"total": 1, "success": 1, "failed": 0}}

        engine.func = emit
        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])
        rendered = result.output + result.stderr

        assert "[OK] vid1 T" in rendered, f"unrendered events: {seen}"
        assert "{" not in rendered, "a raw payload dict leaked into the output"

    def test_failure_exits_with_code_1(self, runner, engine, tmp_path):
        engine.result = {"status": False, "message": "boom"}

        result = runner.invoke(cli, ["channel-full", "https://x/@ch", "--out", str(tmp_path)])

        assert result.exit_code == 1

    def test_missing_url_is_a_usage_error(self, runner, engine):
        assert runner.invoke(cli, ["channel-full"]).exit_code == 2


class TestChannelVideoCommand:
    @pytest.fixture
    def engine(self, monkeypatch):
        rec = _Recorder()
        monkeypatch.setattr("rch.youtube.channel.channel_video", rec)
        return rec

    def test_success_writes_report(self, runner, engine, tmp_path):
        result = runner.invoke(cli, ["channel-video", "https://x/@ch", "--out", str(tmp_path)])

        assert result.exit_code == 0
        assert (tmp_path / "report.txt").exists()

    def test_out_option_reaches_the_engine(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-video", "https://x/@ch", "--out", str(tmp_path)])

        assert engine.last_options["outputDir"] == str(tmp_path)

    def test_extra_positional_is_rejected(self, runner, engine, tmp_path):
        assert runner.invoke(
            cli, ["channel-video", "https://x/@ch", str(tmp_path)]
        ).exit_code == 2

    def test_history_records_command_name(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-video", "https://x/@ch", "--out", str(tmp_path)])

        assert "channel-video" in (tmp_path / "history.log").read_text(encoding="utf-8")

    def test_failure_exits_with_code_1(self, runner, engine, tmp_path):
        engine.result = {"status": False, "message": "boom"}

        assert runner.invoke(
            cli, ["channel-video", "https://x/@ch", "--out", str(tmp_path)]
        ).exit_code == 1


# ---------------------------------------------------------------------------
# channel-info
# ---------------------------------------------------------------------------


class TestChannelInfoCommand:
    @pytest.fixture
    def engine(self, monkeypatch):
        rec = _Recorder({"status": True, "result": {"videos": {}, "total": 0}})
        monkeypatch.setattr("rch.youtube.channel.channel_info", rec)
        return rec

    def test_prints_json_payload(self, runner, engine):
        result = runner.invoke(cli, ["channel-info", "https://x/@ch"])

        assert result.exit_code == 0
        assert '"videos"' in result.output

    def test_output_is_valid_json(self, runner, engine):
        result = runner.invoke(cli, ["channel-info", "https://x/@ch"])

        assert json.loads(result.output) == {"videos": {}, "total": 0}

    def test_out_option_reaches_the_engine(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-info", "https://x/@ch", "--out", str(tmp_path)])

        assert engine.last_options["outputDir"] == str(tmp_path)

    def test_size_positional_reaches_the_engine(self, runner, engine):
        runner.invoke(cli, ["channel-info", "https://x/@ch", "sddefault"])

        assert engine.last_options["size"] == "sddefault"

    def test_second_positional_is_the_size_not_an_output_dir(self, runner, engine):
        result = runner.invoke(cli, ["channel-info", "https://x/@ch", "hqdefault"])

        assert result.exit_code == 0
        assert engine.last_options["size"] == "hqdefault"
        assert engine.last_options["outputDir"] == cli_mod.DEFAULT_OUT

    def test_csv_export_from_videos(self, runner, engine, tmp_path):
        engine.result = {"status": True, "result": {"videos": {"vid1": {"title": "T"}}}}
        csv_file = tmp_path / "m.csv"

        result = runner.invoke(cli, ["channel-info", "https://x/@ch", "--csv", str(csv_file)])

        assert "CSV:" in result.output
        assert "T" in csv_file.read_text(encoding="utf-8")

    def test_json_export_from_videos(self, runner, engine, tmp_path):
        engine.result = {"status": True, "result": {"videos": {"vid1": {"title": "T"}}}}
        json_file = tmp_path / "m.json"

        result = runner.invoke(cli, ["channel-info", "https://x/@ch", "--json", str(json_file)])

        assert "JSON:" in result.output

    def test_export_tolerates_missing_videos_key(self, runner, engine, tmp_path):
        engine.result = {"status": True, "result": {"total": 0}}
        json_file = tmp_path / "m.json"

        result = runner.invoke(cli, ["channel-info", "https://x/@ch", "--json", str(json_file)])

        assert result.exit_code == 0

    def test_failure_exits_with_code_1(self, runner, engine, tmp_path):
        engine.result = {"status": False, "message": "boom"}

        assert runner.invoke(
            cli, ["channel-info", "https://x/@ch", "--out", str(tmp_path)]
        ).exit_code == 1

    def test_no_report_is_written_on_success(self, runner, engine, tmp_path):
        runner.invoke(cli, ["channel-info", "https://x/@ch", "--out", str(tmp_path)])

        assert not (tmp_path / "report.txt").exists()


# ---------------------------------------------------------------------------
# web / gui / serve
# ---------------------------------------------------------------------------


class TestWebCommands:
    @pytest.mark.parametrize("command", ["web", "gui", "serve"])
    def test_delegates_to_run_server(self, runner, monkeypatch, command):
        seen = {}
        monkeypatch.setattr("rch.web.server.run_server",
                            lambda **kw: seen.setdefault("kw", kw))

        runner.invoke(cli, [command])

        assert seen["kw"] == {"host": cli_mod.DEFAULT_WEB_HOST, "port": cli_mod.DEFAULT_WEB_PORT}

    @pytest.mark.parametrize("command", ["web", "gui", "serve"])
    def test_host_and_port_flags_are_forwarded(self, runner, monkeypatch, command):
        seen = {}
        monkeypatch.setattr("rch.web.server.run_server",
                            lambda **kw: seen.setdefault("kw", kw))

        runner.invoke(cli, [command, "--host", "0.0.0.0", "--port", "9999"])

        assert seen["kw"] == {"host": "0.0.0.0", "port": 9999}

    @pytest.mark.parametrize("command", ["web", "gui", "serve"])
    def test_non_integer_port_is_a_usage_error(self, runner, monkeypatch, command):
        monkeypatch.setattr("rch.web.server.run_server", lambda **kw: None)

        assert runner.invoke(cli, [command, "--port", "abc"]).exit_code == 2