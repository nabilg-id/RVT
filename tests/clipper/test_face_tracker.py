"""Tests for :mod:`clipper.services.face_tracker`.

The MediaPipe ``solutions`` API no longer ships in any published release, so
this module uses ``mediapipe.tasks`` instead. These tests pin the parts that
must survive that migration: the detection output contract, graceful
degradation when no model is available, coordinate scaling, and trajectory
smoothing.
"""
from __future__ import annotations

import types

import numpy as np
import pytest

from clipper.services import face_tracker as FT


class _Box:
    def __init__(self, origin_x, origin_y, width, height):
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.width = width
        self.height = height


class _Cat:
    def __init__(self, score):
        self.score = score


class _Det:
    def __init__(self, box, score):
        self.bounding_box = box
        self.categories = [_Cat(score)]


class _Result:
    def __init__(self, detections):
        self.detections = detections


def _frame(h=480, w=640):
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.fixture()
def tracker(monkeypatch):
    """A FaceTracker whose detector is a stub, so no model file is needed."""
    t = FT.FaceTracker(auto_download=False)
    t.available = True
    t.detector = types.SimpleNamespace(detect=lambda image: _Result([]))
    return t


class TestModelResolution:
    def test_existing_model_is_reused(self, tmp_path):
        model = tmp_path / "m.tflite"
        model.write_bytes(b"x")
        assert FT.ensure_model(model) == model

    def test_missing_model_without_download_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FT.ensure_model(tmp_path / "absent.tflite", download=False)

    def test_default_path_lives_under_asset(self):
        assert FT.ensure_model.__module__ == FT.__name__
        assert FT.MODEL_FILENAME.endswith(".tflite")


class TestGracefulDegradation:
    def test_missing_model_disables_tracking_instead_of_raising(self, tmp_path):
        t = FT.FaceTracker(model_path=tmp_path / "nope.tflite", auto_download=False)
        assert t.available is False
        assert t.disabled_reason

    def test_disabled_tracker_returns_no_faces(self, tmp_path):
        t = FT.FaceTracker(model_path=tmp_path / "nope.tflite", auto_download=False)
        assert t.detect_faces_in_frame(_frame()) == []

    def test_disabled_tracker_centre_crops(self, tmp_path):
        class FakeClip:
            size = (1920, 1080)
            duration = 30.0

            def crop(self, x1=None, width=None):
                return ("cropped", x1, width)

        t = FT.FaceTracker(model_path=tmp_path / "nope.tflite", auto_download=False)
        kind, x1, width = t.track_and_crop(FakeClip())
        assert kind == "cropped"
        # int(1080 * 9 / 16) is 607, made even for the encoder, so 606.
        assert width == 606
        assert x1 == (1920 - 606) // 2

    def test_already_vertical_clip_is_left_alone(self, tmp_path):
        clip = types.SimpleNamespace(size=(600, 1080))
        t = FT.FaceTracker(model_path=tmp_path / "nope.tflite", auto_download=False)
        # A 600-wide source is narrower than the 606-wide 9:16 target, so the
        # clip is already vertical and must be handed back untouched.
        assert t.track_and_crop(clip) is clip


class TestDetectContract:
    def test_empty_result_gives_empty_list(self, tracker):
        assert tracker.detect_faces_in_frame(_frame()) == []

    def test_detection_is_scaled_back_to_the_original_frame(self, tracker):
        # Detector works on a half-size frame, so a 100px box there is 200px here.
        tracker.detector = types.SimpleNamespace(
            detect=lambda image: _Result([_Det(_Box(10, 20, 100, 50), 0.9)])
        )
        faces = tracker.detect_faces_in_frame(_frame(h=480, w=640))
        assert len(faces) == 1
        assert faces[0]["width"] == 200
        assert faces[0]["height"] == 100
        # origin 10 -> 20, width 100 -> 200, centre 20 + 200 // 2.
        assert faces[0]["center_x"] == 120

    def test_face_dict_has_every_required_key(self, tracker):
        tracker.detector = types.SimpleNamespace(
            detect=lambda image: _Result([_Det(_Box(1, 2, 3, 4), 0.5)])
        )
        face = tracker.detect_faces_in_frame(_frame())[0]
        for key in ("center_x", "center_y", "width", "height",
                    "confidence", "area"):
            assert key in face
        assert face["area"] == face["width"] * face["height"]

    def test_highest_confidence_times_area_ranks_first(self, tracker):
        tracker.detector = types.SimpleNamespace(
            detect=lambda image: _Result([
                _Det(_Box(0, 0, 10, 10), 0.4),     # score 40
                _Det(_Box(0, 0, 100, 100), 0.9),  # score 9000
            ])
        )
        faces = tracker.detect_faces_in_frame(_frame())
        assert faces[0]["confidence"] == 0.9

    def test_missing_categories_default_to_zero_confidence(self, tracker):
        detection = _Det(_Box(0, 0, 10, 10), 0)
        detection.categories = None
        tracker.detector = types.SimpleNamespace(
            detect=lambda image: _Result([detection])
        )
        assert tracker.detect_faces_in_frame(_frame())[0]["confidence"] == 0.0

    def test_detector_without_detections_attribute(self, tracker):
        tracker.detector = types.SimpleNamespace(detect=lambda image: object())
        assert tracker.detect_faces_in_frame(_frame()) == []

    def test_detector_exception_is_swallowed(self, tracker):
        def _boom(image):
            raise RuntimeError("tflite error")

        tracker.detector = types.SimpleNamespace(detect=_boom)
        assert tracker.detect_faces_in_frame(_frame()) == []

    def test_none_frame_returns_empty(self, tracker):
        assert tracker.detect_faces_in_frame(None) == []

    def test_empty_frame_returns_empty(self, tracker):
        assert tracker.detect_faces_in_frame(np.zeros((0, 0, 3), dtype=np.uint8)) == []


class TestCaching:
    def test_same_timestamp_is_served_from_cache(self, tracker):
        calls = []

        def _counting(image):
            calls.append(1)
            return _Result([])

        tracker.detector = types.SimpleNamespace(detect=_counting)
        tracker.detect_faces_in_frame(_frame(), frame_time=1.0)
        tracker.detect_faces_in_frame(_frame(), frame_time=1.0)
        assert len(calls) == 1

    def test_different_timestamps_are_detected_again(self, tracker):
        calls = []

        def _counting(image):
            calls.append(1)
            return _Result([])

        tracker.detector = types.SimpleNamespace(detect=_counting)
        tracker.detect_faces_in_frame(_frame(), frame_time=1.0)
        tracker.detect_faces_in_frame(_frame(), frame_time=2.0)
        assert len(calls) == 2

    def test_no_timestamp_bypasses_cache(self, tracker):
        calls = []

        def _counting(image):
            calls.append(1)
            return _Result([])

        tracker.detector = types.SimpleNamespace(detect=_counting)
        tracker.detect_faces_in_frame(_frame())
        tracker.detect_faces_in_frame(_frame())
        assert len(calls) == 2


class TestSmoothing:
    def test_short_series_is_returned_unchanged(self, tracker):
        positions = [(1, 2), (3, 4)]
        assert tracker.smooth_trajectory(positions, window_size=5) == positions

    def test_average_is_applied_over_the_window(self, tracker):
        # window 3 covers i-1..i+1, so the middle of a 4-point spike averages
        # its neighbours. Length must exceed the window or it passes through.
        smoothed = tracker.smooth_trajectory(
            [(0, 0), (30, 0), (0, 0), (0, 0)], window_size=3
        )
        assert smoothed[1] == (pytest.approx(10.0), pytest.approx(0))
        assert smoothed[2] == (pytest.approx(10.0), pytest.approx(0))

    def test_length_is_preserved(self, tracker):
        positions = [(i, i) for i in range(10)]
        assert len(tracker.smooth_trajectory(positions, window_size=3)) == 10

    def test_edges_use_partial_windows(self, tracker):
        smoothed = tracker.smooth_trajectory(
            [(0, 0), (4, 0), (8, 0), (12, 0)], window_size=3
        )
        # At i=0 the window is just the first two points: (0+4)/2.
        assert smoothed[0] == (pytest.approx(2.0), pytest.approx(0))
        # At i=3 the window is the last two: (8+12)/2.
        assert smoothed[3] == (pytest.approx(10.0), pytest.approx(0))


class TestClose:
    def test_close_is_safe_without_a_detector(self, tmp_path):
        t = FT.FaceTracker(model_path=tmp_path / "nope.tflite", auto_download=False)
        t.close()
        assert t.available is False

    def test_close_drops_the_cache(self, tracker):
        tracker.detect_faces_in_frame(_frame(), frame_time=1.0)
        assert tracker.face_cache
        tracker.close()
        assert not tracker.face_cache

    def test_close_swallows_cleanup_errors(self, tracker):
        class Grumpy:
            def close(self):
                raise RuntimeError("nope")

        tracker.detector = Grumpy()
        tracker.close()


class TestSampleCount:
    @pytest.mark.parametrize(
        "duration,expected",
        [(3, 3), (9, 3), (12, 6), (30, 7), (100, 8), (1, 3)],
    )
    def test_sample_count_stays_bounded(self, duration, expected):
        assert FT.FaceTracker._sample_count(duration) == expected