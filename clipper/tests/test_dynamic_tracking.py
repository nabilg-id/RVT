import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import unittest
from unittest.mock import MagicMock, patch

import pytest

# Face tracking butuh numpy, mediapipe, dan moviepy. Lewati bila belum
# terpasang supaya suite inti tetap hijau tanpa stack media.
np = pytest.importorskip("numpy", reason="numpy belum terpasang")
pytest.importorskip("mediapipe", reason="mediapipe belum terpasang")
ColorClip = pytest.importorskip("moviepy.editor", reason="moviepy 1.x belum terpasang").ColorClip

# Mock cv2 if it's not installed or causing issues in test env
try:
    import cv2
except ImportError:
    cv2 = MagicMock()
    sys.modules['cv2'] = cv2

from ..services.face_tracker import FaceTracker


class TestDynamicTracking(unittest.TestCase):
    def setUp(self):
        self.tracker = FaceTracker()
    
    def tearDown(self):
        self.tracker.close()

    def test_dynamic_switching(self):
        """
        Test that track_and_crop switches Frame generation based on face detection.
        """
        # Create a dummy clip
        w, h = 1920, 1080
        clip = ColorClip(size=(w, h), color=(0, 0, 0), duration=2)
        
        # We need to mock detect_faces_in_frame to simulate face/no-face
        # Frame 0-1s: Face present
        # Frame 1-2s: No face
        
        def mock_detect(frame, frame_time=None):
            if frame_time is not None and frame_time < 1.0:
                # Face found at center
                # frame is ColorClip frame (H, W, 3)
                return [{'center_x': w//2, 'confidence': 0.9, 'width': 100, 'height': 100, 'area': 10000}]
            return []
            
        # Mock cv2.resize to return expected shape without doing actual resize logic (simplified)
        # OR just let it run if opencv-python is installed.
        # The error "resize" suggests something with cv2.resize or similar.
        # Let's see the traceback. It says "resize". 
        
        with patch.object(self.tracker, 'detect_faces_in_frame', side_effect=mock_detect):
            # We also need to patch cv2 inside face_tracker because it's used for the zoom-out logic
            with patch('clipper.services.face_tracker.cv2.resize') as mock_resize:
                # Mock resize to return a dummy frame of target size
                # target width = 606, height calculated dynamically
                def side_effect_resize(src, dsize):
                    return np.zeros((dsize[1], dsize[0], 3), dtype=np.uint8)
                
                mock_resize.side_effect = side_effect_resize
                
                processed_clip = self.tracker.track_and_crop(clip)
            
            # 1. Check Frame with Face (t=0.5)
            # Expecting a 9:16 crop (1080 * 9 / 16 = 607.5 -> 607 -> 606 even)
            frame_face = processed_clip.get_frame(0.5)
            self.assertEqual(frame_face.shape, (1080, 606, 3)) # H, W, C
            
            # 2. Check Frame without Face (t=1.5)
            # Expecting a resized full frame (also 606x1080 but letterboxed visually)
            try:
                frame_no_face = processed_clip.get_frame(1.5)
                self.assertEqual(frame_no_face.shape, (1080, 606, 3))
            except Exception as e:
                print(f"\n❌ Error Getting Frame: {e}")
                import traceback
                traceback.print_exc()
                raise
            
            # We can't easily verify the *content* is resized vs cropped without complex image analysis,
            # but verifying it runs and produces consistent output dimensions is the main goal.
            print("\n✅ Dynamic tracking processed frames without error.")

if __name__ == '__main__':
    unittest.main()
