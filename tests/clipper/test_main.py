"""Tests for :mod:`clipper.main` — the interactive CLI entry point.

The prompts are answered with monkeypatched ``input`` so the whole flow runs
without a media stack, and ``VideoProcessor`` is stubbed because the real one
loads Whisper and OpenCV.
"""
from __future__ import annotations

import builtins
import sys
import types

import pytest

from clipper import main as M


@pytest.fixture(autouse=True)
def _no_heavy_imports(monkeypatch):
    """Stub the heavy processor so importing clipper.main stays cheap."""
    mod = types.ModuleType("clipper.services.video_processor")

    class FakeProcessor:
        def __init__(self, caption_style="clean_white"):
            self.caption_style = caption_style
            self.caption_maker = types.SimpleNamespace(
                selected_style=caption_style,
                styles={"clean_white": {"name": "Clean White"}},
            )

        def process_video(self, url, num_clips, lo, hi):
            from clipper.config import OUTPUT_DIR
            return [str(OUTPUT_DIR / "clip_1_88pts_v.mp4")], "Judul Uji"

    mod.VideoProcessor = FakeProcessor
    monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)


def _answers(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(it))


class TestValidateYoutubeUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "http://m.youtube.com/watch?v=abc",
            "https://music.youtube.com/watch?v=abc",
        ],
    )
    def test_accepts_youtube_links(self, url):
        assert M.validate_youtube_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            None,
            "not-a-url",
            "https://vimeo.com/12345",
            "ftp://youtube.com/watch?v=abc",
            "javascript:alert(1)",
        ],
    )
    def test_rejects_everything_else(self, url):
        assert M.validate_youtube_url(url) is False


class TestCaptionStyleListing:
    def test_lists_every_style_key(self, capsys):
        keys = M.display_caption_styles()
        assert "clean_white" in keys
        assert "viral_yellow" in keys
        out = capsys.readouterr().out
        assert "Clean White" in out


class TestMainFlow:
    def test_no_url_aborts(self, monkeypatch, capsys):
        _answers(monkeypatch, [""])
        M.main(url="")
        assert "No URL provided" in capsys.readouterr().out

    def test_invalid_url_aborts(self, capsys):
        M.main(url="https://vimeo.com/1")
        assert "Invalid YouTube URL" in capsys.readouterr().out

    def test_happy_path_runs_processor(self, monkeypatch, capsys):
        _answers(monkeypatch, ["3", "60", "20", "1"])
        M.main(url="https://youtu.be/dQw4w9WgXcQ")
        out = capsys.readouterr().out
        assert "VIRAL CLIPS GENERATED" in out
        assert "Judul Uji" in out

    def test_min_greater_than_max_is_rejected(self, monkeypatch, capsys):
        _answers(monkeypatch, ["3", "20", "90", "1"])
        M.main(url="https://youtu.be/dQw4w9WgXcQ")
        assert "Min duration must be less than max duration" in capsys.readouterr().out

    def test_out_of_range_style_choice_falls_back(self, monkeypatch, capsys):
        _answers(monkeypatch, ["3", "60", "20", "99"])
        M.main(url="https://youtu.be/dQw4w9WgXcQ")
        out = capsys.readouterr().out
        assert "Invalid choice" in out
        assert "VIRAL CLIPS GENERATED" in out

    def test_non_numeric_style_choice_falls_back(self, monkeypatch, capsys):
        _answers(monkeypatch, ["3", "60", "20", "abc"])
        M.main(url="https://youtu.be/dQw4w9WgXcQ")
        assert "Invalid input" in capsys.readouterr().out

    def test_processor_exception_is_caught(self, monkeypatch, capsys):
        mod = types.ModuleType("clipper.services.video_processor")

        class Boom:
            def __init__(self, caption_style="clean_white"):
                raise RuntimeError("gagal inisialisasi")

        mod.VideoProcessor = Boom
        monkeypatch.setitem(sys.modules, "clipper.services.video_processor", mod)

        _answers(monkeypatch, ["3", "60", "20", "1"])
        M.main(url="https://youtu.be/dQw4w9WgXcQ")
        assert "unexpected error" in capsys.readouterr().out
