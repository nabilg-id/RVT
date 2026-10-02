"""Tests for :mod:`clipper.utils.helpers`.

These helpers only need the standard library, so they are testable without the
media stack. ``generate_random_clips`` is the fallback path used when
transcription or AI selection fails, so its bounds need pinning.
"""
from __future__ import annotations

import pytest

from clipper.utils import helpers as H


class TestGenerateRandomClips:
    def test_returns_requested_number_of_clips(self):
        clips = H.generate_random_clips(600, 3, 20, 60)
        assert len(clips) == 3

    def test_each_clip_has_the_full_contract(self):
        clip = H.generate_random_clips(600, 1, 20, 60)[0]
        for key in ("start", "end", "title", "virality_score",
                    "hook_type", "duration"):
            assert key in clip

    def test_every_clip_respects_the_duration_bounds(self):
        for clip in H.generate_random_clips(900, 8, 20, 60):
            assert 20 <= clip["duration"] <= 60

    def test_every_clip_stays_inside_the_video(self):
        duration = 600.0
        for clip in H.generate_random_clips(duration, 8, 20, 60):
            assert clip["start"] >= 0
            assert clip["end"] <= duration

    def test_end_is_start_plus_duration(self):
        for clip in H.generate_random_clips(600, 5, 20, 60):
            assert clip["end"] == pytest.approx(clip["start"] + clip["duration"])

    def test_video_shorter_than_a_clip_yields_nothing(self):
        assert H.generate_random_clips(10, 3, 20, 60) == []

    def test_zero_clip_request_returns_empty(self):
        assert H.generate_random_clips(600, 0, 20, 60) == []

    def test_titles_are_numbered_and_distinct(self):
        clips = H.generate_random_clips(600, 4, 20, 60)
        titles = [c["title"] for c in clips]
        assert len(set(titles)) == 4

    def test_fallback_score_is_low_but_not_zero(self):
        for clip in H.generate_random_clips(600, 3, 20, 60):
            assert 0 < clip["virality_score"] < 100


class TestCleanupTempFiles:
    def test_removes_files_but_leaves_subdirectories(self, tmp_path, monkeypatch):
        monkeypatch.setattr(H, "TEMP_DIR", tmp_path)
        (tmp_path / "a.mp4").write_bytes(b"x")
        (tmp_path / "b.txt").write_bytes(b"y")
        (tmp_path / "sub").mkdir()

        H.cleanup_temp_files()

        assert not (tmp_path / "a.mp4").exists()
        assert not (tmp_path / "b.txt").exists()
        assert (tmp_path / "sub").is_dir()

    def test_missing_directory_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(H, "TEMP_DIR", tmp_path / "tidak-ada")
        H.cleanup_temp_files()

    def test_empty_directory_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(H, "TEMP_DIR", tmp_path)
        H.cleanup_temp_files()