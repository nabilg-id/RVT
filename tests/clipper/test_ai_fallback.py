"""Clip selection must always return something usable.

select_clips catches every exception and falls back, so this path is what runs
whenever the model is unavailable, rate limited, or answers with something
unparsable. It used to be able to return an empty list, which propagated to
"Could not select any clips from the video" and failed the whole job on a video
that was perfectly fine.
"""
from __future__ import annotations

import types

import pytest

from clipper.services.ai_selector import AISelector


@pytest.fixture()
def selector():
    """A real AISelector with only the HTTP client replaced."""
    return object.__new__(AISelector)


def seg(start, end, text="kalimat"):
    return {"start": start, "end": end, "text": text}


def fine_segments(duration=60.0):
    """Segments comfortably shorter than a typical max_dur."""
    return [seg(i * 3.0, i * 3.0 + 2.5) for i in range(int(duration // 3))]


class TestAccumulateSegments:
    def test_uses_whole_segments_when_they_fit(self, selector):
        clips = selector._fallback_selection(fine_segments(), 60.0, 2, 4, 8)
        assert len(clips) == 2
        for c in clips:
            assert 4 <= c["end"] - c["start"] <= 8

    def test_respects_the_requested_count(self, selector):
        clips = selector._fallback_selection(fine_segments(), 60.0, 3, 4, 8)
        assert len(clips) == 3

    def test_does_not_reuse_a_segment(self, selector):
        clips = selector._fallback_selection(fine_segments(), 60.0, 3, 4, 8)
        windows = [(c["start"], c["end"]) for c in clips]
        assert len(set(windows)) == len(windows)


class TestOverLongSegments:
    """The regression: a segment longer than max_dur could never be used."""

    def test_single_long_segment_still_yields_clips(self, selector):
        segments = [seg(0.0, 40.0)]
        clips = selector._fallback_selection(segments, 40.0, 2, 4, 8)
        assert len(clips) >= 1
        for c in clips:
            assert 0 < c["end"] - c["start"] <= 8

    def test_all_segments_too_long(self, selector):
        segments = [seg(0.0, 11.0), seg(11.0, 19.0)]
        clips = selector._fallback_selection(segments, 19.0, 2, 4, 8)
        assert clips, "must not return empty when every segment is too long"
        for c in clips:
            assert c["end"] <= 19.0
            assert c["end"] - c["start"] <= 8

    def test_windows_stay_inside_their_segment(self, selector):
        segments = [seg(0.0, 40.0)]
        clips = selector._fallback_selection(segments, 40.0, 4, 4, 8)
        for c in clips:
            assert c["start"] >= 0.0
            assert c["end"] <= 40.0

    def test_never_exceeds_the_video_duration(self, selector):
        segments = [seg(0.0, 100.0)]
        clips = selector._fallback_selection(segments, 100.0, 5, 4, 8)
        for c in clips:
            assert c["end"] <= 100.0


class TestGuaranteedNonEmpty:
    def test_no_segments_still_returns_clips(self, selector):
        clips = selector._fallback_selection([], 30.0, 2, 4, 8)
        assert len(clips) == 2

    def test_empty_transcript_does_not_fail_the_job(self, selector):
        """Whisper returns empty for a silent video; the job must still finish."""
        clips = selector._fallback_selection([], 30.0, 1, 4, 8)
        assert clips
        assert clips[0]["end"] > clips[0]["start"]

    def test_zero_duration_has_nothing_to_offer(self, selector):
        assert selector._fallback_selection([], 0.0, 2, 4, 8) == []

    def test_negative_duration_has_nothing_to_offer(self, selector):
        assert selector._fallback_selection([], -5.0, 2, 4, 8) == []

    def test_even_windows_stay_within_the_video(self, selector):
        clips = selector._even_windows(20.0, 3, 4, 8)
        assert len(clips) == 3
        for c in clips:
            assert 0 <= c["start"] < c["end"] <= 20.0

    def test_even_windows_shrink_for_a_short_video(self, selector):
        clips = selector._even_windows(5.0, 3, 4, 8)
        for c in clips:
            assert c["end"] - c["start"] <= 5.0

    def test_one_window_for_a_one_clip_request(self, selector):
        assert len(selector._even_windows(60.0, 1, 4, 8)) == 1

    def test_requesting_zero_clips_is_honoured(self, selector):
        assert selector._even_windows(60.0, 0, 4, 8) == []


class TestClipShape:
    def test_every_clip_has_the_fields_the_pipeline_reads(self, selector):
        segments = fine_segments()
        for c in selector._fallback_selection(segments, 60.0, 2, 4, 8):
            assert set(c) >= {"start", "end", "title", "virality_score",
                              "hook_type", "duration"}
            assert c["duration"] == c["end"] - c["start"]

    def test_titles_are_sequential(self, selector):
        clips = selector._fallback_selection(fine_segments(), 60.0, 3, 4, 8)
        assert clips[0]["title"] == "Fallback clip 1"
        assert clips[-1]["title"].endswith(str(len(clips)))


class TestSelectClipsDegradation:
    def test_a_broken_client_degrades_to_clips(self):
        """End to end through the public method, with the HTTP layer broken."""

        def _explode(*a, **k):
            raise RuntimeError("model not found")

        selector = object.__new__(AISelector)
        selector.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=_explode)
            )
        )
        selector.model = "openrouter/free"

        out = selector.select_clips(fine_segments(), 60.0, 2, 4, 8)
        assert len(out) == 2
        assert all(c["end"] > c["start"] for c in out)

    def test_a_broken_client_on_a_silent_video_still_yields_clips(self):
        def _explode(*a, **k):
            raise RuntimeError("boom")

        selector = object.__new__(AISelector)
        selector.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=_explode)
            )
        )
        selector.model = "openrouter/free"

        out = selector.select_clips([], 30.0, 1, 4, 8)
        assert out, "a silent video must not fail the job"