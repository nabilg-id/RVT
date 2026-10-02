"""Salvaging the model's answer instead of throwing it away.

An answer used to be accepted only if every clip already fitted the duration
bounds. One clip coming back at 12s against an 8s ceiling discarded the whole
response, and the job fell back to random selection - throwing away the model's
actual choice because of a rounding difference.
"""
from __future__ import annotations

import types

from clipper.services.ai_selector import AISelector

MIN, MAX = 4.0, 8.0
DURATION = 60.0


def clip(start, end, score=70, title="Titel", hook=None):
    out = {"start": start, "end": end, "title": title,
           "virality_score": score, "hook_type": "general"}
    if hook:
        out["hook_segment"] = {"start": hook[0], "end": hook[1]}
    return out


class TestSalvage:
    def test_too_long_clip_is_shortened(self):
        out = AISelector._salvage([clip(0.0, 12.0)], DURATION, MIN, MAX)
        assert len(out) == 1
        assert out[0]["end"] - out[0]["start"] == MAX

    def test_too_short_clip_is_lengthened(self):
        out = AISelector._salvage([clip(10.0, 11.0)], DURATION, MIN, MAX)
        assert out[0]["end"] - out[0]["start"] >= MIN

    def test_start_past_the_end_of_the_video_is_pulled_back(self):
        out = AISelector._salvage([clip(90.0, 100.0)], DURATION, MIN, MAX)
        assert out
        assert out[0]["end"] <= DURATION
        assert out[0]["start"] < out[0]["end"]

    def test_negative_start_is_clamped(self):
        out = AISelector._salvage([clip(-5.0, 3.0)], DURATION, MIN, MAX)
        assert out[0]["start"] >= 0.0

    def test_well_formed_clip_is_untouched(self):
        out = AISelector._salvage([clip(10.0, 14.0)], DURATION, MIN, MAX)
        assert (out[0]["start"], out[0]["end"]) == (10.0, 14.0)

    def test_title_and_score_survive(self):
        out = AISelector._salvage(
            [clip(0.0, 12.0, score=91, title="Hook veiled")], DURATION, MIN, MAX
        )
        assert out[0]["title"] == "Hook veiled"
        assert out[0]["virality_score"] == 91

    def test_hook_is_pulled_inside_the_clip(self):
        out = AISelector._salvage(
            [clip(10.0, 30.0, hook=(40.0, 45.0))], DURATION, MIN, MAX
        )
        hook = out[0]["hook_segment"]
        assert out[0]["start"] <= hook["start"] <= hook["end"] <= out[0]["end"]

    def test_unparsable_timestamps_are_dropped(self):
        assert AISelector._salvage(
            [{"start": "abc", "end": None}], DURATION, MIN, MAX
        ) == []

    def test_missing_timestamps_are_dropped(self):
        assert AISelector._salvage([{"title": "x"}], DURATION, MIN, MAX) == []

    def test_backwards_window_is_dropped(self):
        assert AISelector._salvage([clip(20.0, 10.0)], DURATION, MIN, MAX) == []

    def test_empty_input_gives_empty_output(self):
        assert AISelector._salvage([], DURATION, MIN, MAX) == []
        assert AISelector._salvage(None, DURATION, MIN, MAX) == []

    def test_impossible_request_is_refused_rather_than_stretched(self):
        # A 4s clip cannot come out of a 3s video. Salvage returns nothing so
        # the caller's fallback can clamp to what the video actually has, rather
        # than inventing time that does not exist.
        assert AISelector._salvage([clip(0.0, 5.0)], 3.0, MIN, MAX) == []

    def test_a_clip_shorter_than_the_video_is_fine(self):
        out = AISelector._salvage([clip(0.0, 5.0)], 20.0, MIN, MAX)
        assert len(out) == 1


def _selector_with(payload):
    """An AISelector whose model returns the given JSON text."""
    selector = object.__new__(AISelector)
    selector.model = "test"

    body = __import__("json").dumps(payload)
    reply = types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=body)
        )]
    )
    selector.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda **k: reply)
        )
    )
    return selector


def _segments():
    return [{"start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"k{i}"}
            for i in range(12)]


class TestSelectClipsUsesSalvage:
    def test_over_long_answer_is_kept_after_salvage(self):
        selector = _selector_with(
            {"clips": [clip(10.0, 25.0, score=88, title="Terlalu panjang")]}
        )
        out = selector.select_clips(_segments(), DURATION, 1, MIN, MAX)
        assert len(out) == 1
        assert out[0]["title"] == "Terlalu panjang"
        assert out[0]["virality_score"] == 88
        assert MIN <= out[0]["end"] - out[0]["start"] <= MAX

    def test_out_of_range_answer_is_kept_after_salvage(self):
        selector = _selector_with(
            {"clips": [clip(500.0, 560.0, title="Di luar video")]}
        )
        out = selector.select_clips(_segments(), DURATION, 1, MIN, MAX)
        assert len(out) == 1
        assert out[0]["end"] <= DURATION

    def test_nothing_usable_still_falls_back(self):
        selector = _selector_with({"clips": [{"title": "tanpa timestamp"}]})
        out = selector.select_clips(_segments(), DURATION, 1, MIN, MAX)
        assert out, "must fall back rather than return nothing"
        assert out[0]["title"].startswith("Fallback")

    def test_malformed_json_falls_back(self):
        selector = _selector_with(None)
        selector.client.chat.completions.create = lambda **k: types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="bukan json"))]
        )
        out = selector.select_clips(_segments(), DURATION, 2, MIN, MAX)
        assert len(out) == 2

    def test_valid_answer_is_used_without_salvage_message(self, capsys):
        selector = _selector_with({"clips": [clip(10.0, 14.0, title="Bagus")]})
        selector.select_clips(_segments(), DURATION, 1, MIN, MAX)
        assert "disesuaikan" not in capsys.readouterr().out