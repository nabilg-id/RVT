"""Unit tests for the rch.cli helper layer — not the click commands themselves.

These cover the pure/near-pure seams that the command callbacks lean on:
``_abs``, ``_build_options`` (the click-params -> engine-options translator),
``_print_progress`` (event wiring), ``_export`` (CSV/JSON side effects) and
``_finish_channel_result`` (the shared channel epilogue that writes the report
and appends to history).

``tests/test_cli_commands.py`` covers the command surface on top of these.
"""
from __future__ import annotations

import os

import pytest

from rch import cli as cli_mod
from rch.cli import _abs, _build_options, _export, _finish_channel_result, _print_progress
from rch.config import CONFIG

_OK_RESULT = {
    "status": True,
    "result": {"total": 2, "success": 2, "failed": 0, "items": []},
}


# ---------------------------------------------------------------------------
# _abs
# ---------------------------------------------------------------------------


class TestAbs:
    def test_returns_absolute_path(self, tmp_path):
        assert os.path.isabs(_abs("./downloads"))

    def test_is_idempotent(self, tmp_path):
        once = _abs("./x")

        assert _abs(once) == once


# ---------------------------------------------------------------------------
# _build_options
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cookie_env(monkeypatch):
    monkeypatch.delenv("RCH_COOKIES", raising=False)


class TestBuildOptions:
    def test_output_dir_defaults(self):
        assert _build_options()["outputDir"] == cli_mod.DEFAULT_OUT

    def test_output_dir_from_out(self):
        assert _build_options(out="/tmp/here")["outputDir"] == "/tmp/here"

    def test_empty_out_falls_back_to_default(self):
        assert _build_options(out="")["outputDir"] == cli_mod.DEFAULT_OUT

    def test_limit_passes_through(self):
        assert _build_options(limit=5)["limit"] == 5

    def test_absent_limit_is_none(self):
        assert _build_options()["limit"] is None

    def test_duration_filters_pass_through(self):
        opts = _build_options(min_duration=60, max_duration=600)

        assert opts["minDuration"] == 60
        assert opts["maxDuration"] == 600

    def test_after_filter_passes_through(self):
        assert _build_options(after="20260101")["after"] == "20260101"

    def test_export_paths_are_renamed(self):
        opts = _build_options(csv_path="a.csv", json_path="a.json")

        assert opts["csv"] == "a.csv"
        assert opts["json"] == "a.json"

    def test_subtitle_options_pass_through(self):
        opts = _build_options(sub_lang="en", subtitles=True)

        assert opts["subLang"] == "en"
        assert opts["subtitles"] is True

    def test_boolean_flags_default_false(self):
        opts = _build_options()

        assert opts["shorts"] is False
        assert opts["subtitles"] is False
        assert opts["resume"] is False

    def test_boolean_flags_are_coerced_to_bool(self):
        opts = _build_options(shorts=1, subtitles="yes", resume="")

        assert opts["shorts"] is True
        assert opts["subtitles"] is True
        assert opts["resume"] is False

    def test_quality_falls_back_to_config(self):
        assert _build_options()["quality"] == CONFIG["quality"]

    def test_concurrency_falls_back_to_config(self):
        assert _build_options()["concurrency"] == CONFIG["concurrency"]

    def test_zero_concurrency_falls_back_to_config(self):
        assert _build_options(concurrency=0)["concurrency"] == CONFIG["concurrency"]

    def test_explicit_quality_wins(self):
        assert _build_options(quality="1080p")["quality"] == "1080p"

    def test_cookies_default_empty(self):
        assert _build_options()["cookies"] == ""

    def test_cookies_exported_to_environment(self):
        _build_options(cookies="chrome")

        assert os.environ["RCH_COOKIES"] == "chrome"

    def test_blank_cookies_does_not_touch_environment(self):
        _build_options(cookies="")

        assert "RCH_COOKIES" not in os.environ

    def test_key_set_is_stable(self):
        assert set(_build_options()) == {
            "outputDir", "limit", "cookies", "quality", "concurrency",
            "minDuration", "maxDuration", "after", "csv", "json", "subLang",
            "shorts", "subtitles", "resume", "size",
        }


# ---------------------------------------------------------------------------
# _print_progress
# ---------------------------------------------------------------------------


class _FakeEmitter:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)


def _bar(ratio, width=30):
    filled = round(min(1.0, ratio) * width)
    return "█" * filled + "░" * (width - filled)


class TestPrintProgress:
    def test_none_emitter_is_a_noop(self):
        _print_progress(None)

    def test_subscribes_to_the_events_the_engine_emits(self):
        emitter = _FakeEmitter()

        _print_progress(emitter)

        # channel.py emits: progress, phase, start, video:start, video:done, complete
        assert set(emitter.handlers) == {"progress", "phase", "video:done"}

    def test_progress_renders_a_bar_with_counts(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 1, "total": 2, "label": "Judul"})

        err = capsys.readouterr().err
        assert _bar(0.5) in err
        assert " 50%" in err
        assert "(1/2)" in err
        assert "Judul" in err

    def test_progress_bar_is_full_at_completion(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 4, "total": 4, "label": ""})

        assert _bar(1.0) in capsys.readouterr().err

    def test_progress_is_capped_at_one_hundred_percent(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 9, "total": 4, "label": ""})

        assert _bar(1.0) in capsys.readouterr().err

    def test_progress_without_total_is_ignored(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 1})

        assert capsys.readouterr().err == ""

    def test_progress_without_total_zero_is_ignored(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 0, "total": 0, "label": ""})

        assert capsys.readouterr().err == ""

    def test_progress_payload_is_never_dumped_raw(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["progress"][0]({"done": 1, "total": 2, "label": "T"})

        assert "'done'" not in capsys.readouterr().err

    def test_long_progress_label_is_truncated(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)
        label = "x" * 60

        emitter.handlers["progress"][0]({"done": 1, "total": 2, "label": label})

        err = capsys.readouterr().err
        assert ("x" * 40 + "...") in err
        assert ("x" * 41) not in err

    def test_phase_shows_the_human_message(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["phase"][0]({"phase": "list", "msg": "Mengambil daftar video"})

        assert "[*] Mengambil daftar video" in capsys.readouterr().err

    def test_phase_without_msg_falls_back_to_phase_key(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["phase"][0]({"phase": "download"})

        assert "[*] download" in capsys.readouterr().err

    def test_phase_payload_is_never_dumped_raw(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["phase"][0]({"phase": "list", "msg": "M"})

        assert "'msg'" not in capsys.readouterr().err

    def test_video_done_ok_shows_id_and_title(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["video:done"][0]({"id": "vid1", "title": "Judul", "ok": True})

        assert "[OK] vid1 Judul" in capsys.readouterr().err

    def test_video_done_failure_shows_id_and_error(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["video:done"][0](
            {"id": "vid2", "title": "Judul", "ok": False, "error": "403"})

        assert "[!!] vid2 403" in capsys.readouterr().err

    def test_video_done_failure_without_error_does_not_raise(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["video:done"][0]({"id": "vid3", "ok": False})

        assert "[!!] vid3" in capsys.readouterr().err

    def test_video_done_without_title_does_not_raise(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["video:done"][0]({"id": "vid4", "ok": True})

        assert "[OK] vid4" in capsys.readouterr().err

    def test_video_done_skipped_is_reported_as_ok(self, capsys):
        emitter = _FakeEmitter()
        _print_progress(emitter)

        emitter.handlers["video:done"][0](
            {"id": "vid5", "title": "T", "ok": True, "skipped": True})

        assert "[OK] vid5" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _export
# ---------------------------------------------------------------------------


class TestExport:
    def test_no_paths_is_a_noop(self, tmp_path, capsys):
        _export({"a": {}}, None, None)

        assert capsys.readouterr().out == ""

    def test_csv_path_is_written_and_echoed(self, tmp_path, capsys):
        csv_file = tmp_path / "meta.csv"

        _export({"v1": {"title": "Judul"}}, csv_file, None)

        assert "Judul" in csv_file.read_text(encoding="utf-8")
        assert f"CSV: {os.path.abspath(csv_file)}" in capsys.readouterr().out

    def test_json_path_is_written_and_echoed(self, tmp_path, capsys):
        json_file = tmp_path / "meta.json"

        _export({"v1": {"title": "Judul"}}, None, json_file)

        assert '"title"' in json_file.read_text(encoding="utf-8")
        assert f"JSON: {os.path.abspath(json_file)}" in capsys.readouterr().out

    def test_both_paths_are_written(self, tmp_path, capsys):
        csv_file = tmp_path / "meta.csv"
        json_file = tmp_path / "meta.json"

        _export({"v1": {"title": "T"}}, csv_file, json_file)

        assert csv_file.exists()
        assert json_file.exists()

    def test_empty_metadata_is_handled(self, tmp_path):
        csv_file = tmp_path / "meta.csv"

        _export({}, csv_file, None)

        assert csv_file.exists()


# ---------------------------------------------------------------------------
# _finish_channel_result
# ---------------------------------------------------------------------------


class TestFinishChannelResult:
    def test_success_prints_totals(self, tmp_path, capsys):
        _finish_channel_result(_OK_RESULT, "channel-full", "URL", str(tmp_path))

        out = capsys.readouterr().out
        assert "Total: 2" in out
        assert "Sukses: 2" in out
        assert "Gagal: 0" in out

    def test_failure_exits_with_code_1(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _finish_channel_result({"status": False, "message": "boom"},
                                   "channel-full", "URL", str(tmp_path))

        assert exc.value.code == 1

    def test_failure_message_is_printed(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _finish_channel_result({"status": False, "message": "boom"},
                                   "channel-full", "URL", str(tmp_path))

        assert "[!] boom" in capsys.readouterr().err

    def test_failure_without_message_uses_generic_text(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _finish_channel_result({"status": False}, "channel-full", "URL", str(tmp_path))

        assert "[!] Gagal" in capsys.readouterr().err

    def test_zip_path_is_echoed_absolute(self, tmp_path, capsys):
        zip_path = tmp_path / "out.zip"
        result = {"status": True, "result": {**_OK_RESULT["result"], "zipPath": zip_path}}

        _finish_channel_result(result, "channel-full", "URL", str(tmp_path))

        assert f"ZIP: {os.path.abspath(zip_path)}" in capsys.readouterr().out

    def test_absent_zip_path_prints_no_zip_line(self, tmp_path, capsys):
        _finish_channel_result(_OK_RESULT, "channel-full", "URL", str(tmp_path))

        assert "ZIP:" not in capsys.readouterr().out

    def test_failed_items_are_listed(self, tmp_path, capsys):
        result = {"status": True, "result": {
            "total": 2, "success": 1, "failed": 1,
            "items": [{"ok": False, "videoId": "vid9", "error": "403"}],
        }}

        _finish_channel_result(result, "channel-full", "URL", str(tmp_path))

        out = capsys.readouterr().out
        assert "Gagal pada:" in out
        assert "vid9 -> 403" in out

    def test_all_ok_items_are_not_listed(self, tmp_path, capsys):
        result = {"status": True, "result": {
            "total": 1, "success": 1, "failed": 0,
            "items": [{"ok": True, "videoId": "vid1"}],
        }}

        _finish_channel_result(result, "channel-full", "URL", str(tmp_path))

        assert "Gagal pada:" not in capsys.readouterr().out

    def test_report_file_is_written(self, tmp_path):
        _finish_channel_result(_OK_RESULT, "channel-full", "https://x/@ch", str(tmp_path))

        content = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert "Total   : 2" in content
        assert "https://x/@ch" in content
        assert "Perintah: channel-full" in content

    def test_history_entry_is_appended(self, tmp_path):
        _finish_channel_result(_OK_RESULT, "channel-video", "https://x/@ch", str(tmp_path))

        line = (tmp_path / "history.log").read_text(encoding="utf-8").strip()
        assert "channel-video" in line
        assert "total=2" in line

    def test_report_records_failed_items(self, tmp_path):
        result = {"status": True, "result": {
            "total": 1, "success": 0, "failed": 1,
            "items": [{"ok": False, "videoId": "vid9", "error": "403"}],
        }}

        _finish_channel_result(result, "channel-full", "URL", str(tmp_path))

        assert "vid9 -> 403" in (tmp_path / "report.txt").read_text(encoding="utf-8")

    def test_output_dir_is_created_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b"

        _finish_channel_result(_OK_RESULT, "channel-full", "URL", str(nested))

        assert (nested / "report.txt").exists()

    def test_report_path_is_echoed(self, tmp_path, capsys):
        _finish_channel_result(_OK_RESULT, "channel-full", "URL", str(tmp_path))

        assert f"Report: {os.path.abspath(tmp_path / 'report.txt')}" in capsys.readouterr().out