"""Tests for :mod:`clipper.progress` — turning pipeline stdout into progress.

The clip pipeline reports progress with ``print()``. These tests pin the
translation layer that turns those existing lines into phase and percentage,
because a silent mismatch there would show a stuck progress bar while the
video was in fact being cut.
"""
from __future__ import annotations

import io
import sys

import pytest

from clipper.progress import Job, StreamCapture, parse_progress

captured: list = []


class TestParseProgress:
    @pytest.mark.parametrize(
        "line,phase,expected",
        [
            ("📥 Downloading video...", "Mengunduh video", 0.05),
            ("📥 Downloading video with yt-dlp (Best Quality)...", "Mengunduh video", 0.05),
            ("🎵 Starting transcription with Whisper...", "Transkripsi Whisper", 0.15),
            ("🧠 Using AI to select the most viral clips...", "AI memilih momen", 0.34),
            ("🎬 Processing 3 viral clips...", "Memotong clip", 0.40),
            ("🎥 Encoding with optimized settings...", "Encoding", 0.80),
            ("✅ Video encoding complete", "Encoding", 0.90),
            ("🎉 VIRAL CLIPS GENERATED!", "Selesai", 1.0),
        ],
    )
    def test_known_lines_map_to_phases(self, line, phase, expected):
        result = parse_progress(line)
        assert result is not None
        assert result[0] == phase
        assert result[1] == pytest.approx(expected)

    def test_unrelated_line_is_ignored(self):
        assert parse_progress("💾 Total Size: 12.34 MB") is None

    def test_empty_line_is_ignored(self):
        assert parse_progress("") is None

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("📹 Clip 1/4: Hook", 0.40),
            ("📹 Clip 2/4: Hook", 0.5125),
            ("📹 Clip 3/4: Hook", 0.625),
            ("📹 Clip 4/4: Hook", 0.7375),
        ],
    )
    def test_clip_counter_advances_within_its_span(self, line, expected):
        result = parse_progress(line)
        assert result is not None
        assert result[0] == "Memotong clip"
        assert result[1] == pytest.approx(expected)

    def test_progress_never_moves_backwards_across_a_run(self):
        lines = [
            "📥 Downloading video...",
            "🎵 Starting transcription with Whisper...",
            "🧠 Using AI to select the most viral clips...",
            "🎬 Processing 2 viral clips...",
            "📹 Clip 1/2: A",
            "📹 Clip 2/2: B",
            "🎥 Encoding with optimized settings...",
            "🎉 VIRAL CLIPS GENERATED!",
        ]
        values = [parse_progress(ln)[1] for ln in lines if parse_progress(ln)]
        assert values == sorted(values), f"progress went backwards: {values}"

    def test_zero_total_does_not_divide_by_zero(self):
        result = parse_progress("📹 Clip 1/0: broken")
        assert result is None or result[1] == pytest.approx(0.40)


class TestJob:
    def test_starts_running_with_zero_progress(self):
        job = Job(1, {"url": "u"})
        snap = job.snapshot()
        assert snap["status"] == "running"
        assert snap["progress"] == 0.0

    def test_log_line_advances_phase_and_progress(self):
        job = Job(1, {})
        job.append_log("🎵 Starting transcription with Whisper...")
        snap = job.snapshot()
        assert snap["phase"] == "Transkripsi Whisper"
        assert snap["progress"] > 0

    def test_noise_line_does_not_change_progress(self):
        job = Job(1, {})
        before = job.snapshot()["progress"]
        job.append_log("💾 Total Size: 1.00 MB")
        assert job.snapshot()["progress"] == before

    def test_finish_marks_done_and_completes(self):
        job = Job(1, {})
        job.finish()
        snap = job.snapshot()
        assert snap["status"] == "done"
        assert snap["progress"] == 1.0

    def test_finish_with_error_marks_error(self):
        job = Job(1, {})
        job.finish(error="boom")
        snap = job.snapshot()
        assert snap["status"] == "error"
        assert snap["error"] == "boom"

    def test_outputs_are_parsed_from_the_generated_clips_block(self):
        job = Job(1, {})
        for line in [
            "🎉 VIRAL CLIPS GENERATED!",
            "📋 Generated Clips:",
            "  - clip_1_88pts_v.mp4 (1.20 MB)",
            "  - clip_2_75pts_v.mp4 (1.05 MB)",
            "",
            "💾 Total Size: 2.25 MB",
        ]:
            job.append_log(line)
        assert job.snapshot()["outputs"] == [
            "clip_1_88pts_v.mp4", "clip_2_75pts_v.mp4",
        ]

    def test_generated_clips_block_absent_yields_no_outputs(self):
        job = Job(1, {})
        job.append_log("no clips here")
        assert job.snapshot()["outputs"] == []

    def test_snapshot_is_detached_from_later_log_writes(self):
        job = Job(1, {})
        job.append_log("first")
        snap = job.snapshot()
        job.append_log("second")
        assert "second" not in snap["log"]

    def test_log_is_truncated_rather_than_growing_without_bound(self):
        job = Job(1, {})
        for i in range(3000):
            job.append_log(f"line {i}")
        snap = job.snapshot(log_tail=200)
        assert len(snap["log"]) == 200
        assert snap["logTruncated"] is True

    def test_title_round_trips(self):
        job = Job(1, {})
        job.set_title("Judul Video")
        assert job.snapshot()["title"] == "Judul Video"


class TestStreamCapture:
    def test_captures_stdout_lines(self, capsys):
        with StreamCapture(lambda ln: captured.append(ln)):
            print("hello from pipeline")
        assert "hello from pipeline" in captured

    def test_captures_stderr_lines(self):
        captured = []
        with StreamCapture(captured.append):
            print("to stderr", file=sys.stderr)
        assert "to stderr" in captured

    def test_restores_both_streams_on_exit(self):
        before_out, before_err = sys.stdout, sys.stderr
        with StreamCapture(lambda ln: None):
            assert sys.stdout is not before_out
        assert sys.stdout is before_out
        assert sys.stderr is before_err

    def test_restores_streams_even_when_body_raises(self):
        before = sys.stdout
        with pytest.raises(ValueError):
            with StreamCapture(lambda ln: None):
                raise ValueError("boom")
        assert sys.stdout is before

    def test_blank_lines_are_not_forwarded(self):
        captured = []
        with StreamCapture(captured.append):
            print("")
            print("   ")
        assert captured == []

    def test_original_stream_still_receives_output(self):
        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            with StreamCapture(lambda ln: None):
                print("visible")
        finally:
            sys.stdout = original
        assert "visible" in buffer.getvalue()

    def test_teed_stream_reports_not_a_tty(self):
        from clipper.progress import _Tee

        tee = _Tee(io.StringIO(), lambda ln: None)
        assert tee.isatty() is False
        assert tee.writable() is True
