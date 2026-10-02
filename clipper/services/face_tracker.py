"""Face detection used to keep the speaker centred in a 9:16 crop.

MediaPipe retired the ``mediapipe.solutions`` API: it is absent from every
``mediapipe`` release still published on PyPI (0.10.30 through 1.0.1), so the
old ``mp.solutions.face_detection`` call raised AttributeError and face
tracking never ran. This uses the supported ``mediapipe.tasks`` API instead.

The model is a 230 KB TFLite file under ``asset/``. When it is absent the
tracker disables itself instead of raising, so a clip can still be produced
with a plain centre crop.
"""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import mediapipe as mp
import numpy as np

MODEL_FILENAME = "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)

_MIN_CONFIDENCE = 0.5
_DETECT_SCALE = 0.5


def _model_path() -> Path:
    from ..config import ASSET_DIR

    return ASSET_DIR / MODEL_FILENAME


def ensure_model(path: Optional[Path] = None, *, download: bool = True) -> Path:
    """Return the model path, downloading it once if it is missing.

    Raises ``FileNotFoundError`` when the model is absent and ``download`` is
    false, so callers can degrade instead of hanging on the network.
    """
    target = Path(path) if path is not None else _model_path()
    if target.exists():
        return target
    if not download:
        raise FileNotFoundError(f"Model face detector tidak ditemukan: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(MODEL_URL, timeout=120) as response:
        tmp.write_bytes(response.read())
    tmp.replace(target)
    return target


class FaceTracker:
    """Detects faces across a clip and crops it to keep the speaker centred."""

    def __init__(self, model_path: Optional[Path] = None,
                 min_detection_confidence: float = _MIN_CONFIDENCE,
                 auto_download: bool = True):
        """Create the detector.

        A missing or unusable model disables face tracking rather than raising:
        the caller still gets a clip, just centre-cropped. ``available`` says
        which happened.
        """
        self.min_detection_confidence = min_detection_confidence
        self.face_cache: Dict = {}
        self._lock = threading.Lock()
        self.detector = None
        self.available = False
        self.disabled_reason: Optional[str] = None

        try:
            resolved = ensure_model(model_path, download=auto_download)
            self._build_detector(resolved)
            self.available = True
            print("🎯 Initialized face tracking with MediaPipe Tasks API")
        except Exception as exc:  # noqa: BLE001 - degrade, never block the pipeline
            self.available = False
            self.disabled_reason = str(exc)
            print(f"⚠️ Face tracking dinonaktifkan: {exc}")

    def _build_detector(self, model_path: Path) -> None:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        options = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            min_detection_confidence=self.min_detection_confidence,
        )
        self.detector = vision.FaceDetector.create_from_options(options)

    # -- detection ----------------------------------------------------------

    def detect_faces_in_frame(self, frame, frame_time=None) -> List[Dict]:
        """Detect faces in one frame.

        Args:
            frame (numpy.ndarray): BGR video frame.
            frame_time (float, optional): Cache key; enables reuse across the
                same timestamp.

        Returns:
            list: Dicts with ``center_x``, ``center_y``, ``width``, ``height``,
            ``confidence`` and ``area``, most confident first. Empty when
            tracking is unavailable or nothing was found.
        """
        if frame_time is not None and frame_time in self.face_cache:
            return self.face_cache[frame_time]

        faces = self._detect(frame)

        if frame_time is not None:
            self.face_cache[frame_time] = faces
        return faces

    def _detect(self, frame) -> List[Dict]:
        if not self.available or self.detector is None:
            return []
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        try:
            height, width = frame.shape[:2]
            small = cv2.resize(frame, (max(1, int(width * _DETECT_SCALE)),
                                         max(1, int(height * _DETECT_SCALE))))
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self.detector.detect(mp_image)

            # The Tasks API reports boxes in pixels of the image it was given,
            # so scale back up to the original frame.
            sx = width / max(1, small.shape[1])
            sy = height / max(1, small.shape[0])

            faces = []
            for detection in getattr(result, "detections", None) or []:
                box = detection.bounding_box
                x = int(box.origin_x * sx)
                y = int(box.origin_y * sy)
                box_w = int(box.width * sx)
                box_h = int(box.height * sy)

                confidence = 0.0
                categories = getattr(detection, "categories", None) or []
                if categories:
                    confidence = float(categories[0].score)

                faces.append({
                    "center_x": x + box_w // 2,
                    "center_y": y + box_h // 2,
                    "width": box_w,
                    "height": box_h,
                    "confidence": confidence,
                    "area": box_w * box_h,
                })

            faces.sort(key=lambda f: f["confidence"] * f["area"], reverse=True)
            return faces
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the run
            print(f"    ⚠️ Face detection error: {exc}")
            return []

    # -- trajectory ---------------------------------------------------------

    def smooth_trajectory(self, positions, window_size=5):
        """Moving-average smoothing over a list of ``(x, y)`` positions."""
        if len(positions) <= window_size:
            return positions

        smoothed = []
        for i in range(len(positions)):
            start_idx = max(0, i - window_size // 2)
            end_idx = min(len(positions), i + window_size // 2 + 1)
            window = positions[start_idx:end_idx]

            avg_x = sum(pos[0] for pos in window) / len(window)
            avg_y = sum(pos[1] for pos in window) / len(window)
            smoothed.append((avg_x, avg_y))

        return smoothed

    # -- cropping -----------------------------------------------------------

    def track_and_crop(self, clip):
        """Crop ``clip`` to 9:16, centred on the detected face.

        Frames without a face fall back to the previous position, and a clip
        with no detection at all is centre-cropped.
        """
        width, height = clip.size
        target_width = int(height * 9 / 16)
        if target_width % 2 != 0:
            target_width -= 1
        if width <= target_width:
            print("    ⏩ Melewati face tracking - video sudah 9:16")
            return clip

        if not self.available:
            print("    ⚠️ Face tracking tidak aktif, memakai center crop")
            return clip.crop(x1=(width - target_width) // 2, width=target_width)

        print("    🎯 Menganalisis frame untuk posisi face tracking terbaik...")

        with self._lock:
            self.face_cache = {}

        face_positions: List[int] = []
        num_samples = self._sample_count(clip.duration)
        print(f"    ⏳ Menganalisis {num_samples} frame dari {clip.duration:.1f}s video...")

        for i, t in enumerate(np.linspace(0, clip.duration, num_samples)):
            try:
                print(f"    ⏳ Memproses frame {i + 1}/{num_samples} pada {t:.2f}s...")
                frame = clip.get_frame(t)
                faces = self.detect_faces_in_frame(frame, frame_time=t)

                if faces:
                    best = faces[0]
                    face_positions.append(best["center_x"])
                    print(f"    ✅ Frame {i + 1}: Face di posisi "
                          f"{best['center_x']} (confidence {best['confidence']:.2f})")
                else:
                    face_positions.append(face_positions[-1] if face_positions
                                          else width // 2)
                    print(f"    ⚠️ Frame {i + 1}: Face tidak terdeteksi")
            except Exception as exc:  # noqa: BLE001 - keep sampling the rest
                print(f"    ⚠️ Gagal proses frame {i + 1} pada {t:.2f}s: {exc}")
                face_positions.append(face_positions[-1] if face_positions
                                      else width // 2)

        if face_positions:
            print("    ⏳ Menghitung lintasan tracking optimal...")
            positions = [(pos, height // 2) for pos in face_positions]
            smoothed = self.smooth_trajectory(positions, window_size=3)
            center_x = int(np.median([pos[0] for pos in smoothed]))
            print(f"    ✅ Face tracking selesai, center optimal: {center_x}")
        else:
            center_x = width // 2
            print("    ⚠️ Tidak ada face terdeteksi, memakai center crop")

        center_x = max(target_width // 2, min(width - target_width // 2, center_x))
        left = center_x - target_width // 2

        print(f"    ⏳ Memotong video ke {target_width}x{height} (9:16) di x={left}")

        with self._lock:
            self.face_cache = {}

        cropped = clip.crop(x1=left, width=target_width)
        print(f"    ✅ Pemotongan selesai: {target_width}x{height}")
        return cropped

    @staticmethod
    def _sample_count(duration: float) -> int:
        if duration > 10:
            return min(8, max(6, int(duration / 4)))
        return min(6, max(3, int(duration / 3)))

    def close(self) -> None:
        """Release the detector and drop the frame cache."""
        with self._lock:
            self.face_cache = {}
        detector = self.detector
        self.detector = None
        self.available = False
        if detector is None:
            return
        for method in ("close", "_reset"):
            closer = getattr(detector, method, None)
            if callable(closer):
                try:
                    closer()
                    break
                except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                    print(f"⚠️ Gagal menutup face tracker: {exc}")
                    break
        print("🎯 Resource face tracking dilepas")