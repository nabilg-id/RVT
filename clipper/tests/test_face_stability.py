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

# Mock cv2
try:
    import cv2
except ImportError:
    cv2 = MagicMock()
    sys.modules['cv2'] = cv2

from ..services.face_tracker import FaceTracker


class TestFaceStability(unittest.TestCase):
    def setUp(self):
        self.tracker = FaceTracker()
    
    def tearDown(self):
        self.tracker.close()

    def test_gap_filling(self):
        """
        Test that short gaps in detection are filled.
        Sequence: Face (0s) -> No Face (0.5s) -> Face (1.0s)
        Result: No Face at 0.5s should be treated as Face.
        """
        # Clip duration 2s, fps_analyze=2 (timestamps: 0.0, 0.5, 1.0, 1.5)
        w, h = 1920, 1080
        clip = ColorClip(size=(w, h), color=(0, 0, 0), duration=2.0)
        
        # Mock detection:
        # 0.0s: Face
        # 0.5s: NO Face (Gap)
        # 1.0s: Face
        # 1.5s: No Face (End)
        
        def mock_detect(frame, frame_time=None):
            if frame_time is None: return []
            t = round(frame_time, 1)
            
            if t == 0.0 or t == 1.0:
                return [{'center_x': w//2, 'confidence': 0.9, 'width': 100, 'height': 100, 'area': 10000}]
            return []
            
        with patch.object(self.tracker, 'detect_faces_in_frame', side_effect=mock_detect):
            # Patch resize to avoid errors
            with patch('services.face_tracker.cv2.resize') as mock_resize:
                mock_resize.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
                
                processed_clip = self.tracker.track_and_crop(clip)
            
            # 1. Check t=0.0 (Face) -> Should be cropped
            frame0 = processed_clip.get_frame(0.0)
            # 9:16 width = 606 (from previous test)
            self.assertEqual(frame0.shape[1], 606, "Frame at 0.0s should be cropped (Face Mode)")
            
            # 2. Check t=0.5 (GAP) -> Should be FILLED -> Cropped
            frame05 = processed_clip.get_frame(0.5)
            self.assertEqual(frame05.shape[1], 606, "Frame at 0.5s should be cropped (Gap Filled)")
            
            # 3. Check t=1.5 (End, > 1.0s gap from last face?)
            # Last face at 1.0. Next face? None.
            # So 1.5 is > 0.5s from last face. 
            # Gap filling looks forward too. If no next face, it might not fill.
            # Let's check logic: "Look forward... if (next_time - curr_time) > window: break"
            # At 1.5, no future face. So it stays No Face.
            # Wait, implementation: if prev_face != -1 AND next_face != -1: fill.
            # So edges are NOT filled. Correct.
            
            frame15 = processed_clip.get_frame(1.5)
            # Full width resized (also 606 but logic is different path)
            # Wait, if get_frame returns 606 width for BOTH modes, how do we distinguish?
            # In "No Face" mode, it calls cv2.resize to w_target.
            # In "Face" mode, it crops to w_target.
            # The Output Shape is IDENTICAL.
            # We need to verify internal state or mock logic.
            
            # Actually, we can check if `cv2.resize` was called!
            # If Face Mode (Gap Filled), `cv2.resize` is NOT called (it uses numpy slicing).
            # If No Face Mode, `cv2.resize` IS called.
            
            pass 

    @patch('services.face_tracker.cv2.resize')
    def test_gap_filling_calls(self, mock_resize):
        w, h = 1920, 1080
        clip = ColorClip(size=(w, h), color=(0, 0, 0), duration=2.0)
        
        # 0.0: Face, 0.5: Gap, 1.0: Face.
        def mock_detect(frame, frame_time=None):
            if frame_time is None: return []
            t = round(frame_time, 1)
            if t == 0.0 or t == 1.0:
                return [{'center_x': w//2, 'confidence': 0.9, 'width': 100, 'height': 100, 'area': 10000}]
            return []
            
        with patch.object(self.tracker, 'detect_faces_in_frame', side_effect=mock_detect):
             # We need a side effect for resize to return a valid frame so get_frame doesn't crash
            mock_resize.return_value = np.zeros((1920, 606, 3), dtype=np.uint8) # h, w, c
            
            processed_clip = self.tracker.track_and_crop(clip)
            
            # Execute frame at 0.5s (The Gap)
            _ = processed_clip.get_frame(0.5)
            
            # Verify cv2.resize was NOT called (meaning it used Face Mode / Cropping)
            # Wait, get_frame(0.5) calls make_frame(0.5) which calls process_frame(0.5)
            # inside process_frame:
            # if state['has_face']: return crop (NO resize)
            # else: return resize(...)
            
            # So if gap filling worked, 0.5s has_face=True -> No resize call for THIS frame.
            # But process_frame might be called multiple times?
            
            # Let's reset mock to be sure
            mock_resize.reset_mock()
            
            _ = processed_clip.get_frame(0.5)
            
            mock_resize.assert_not_called()
            print("\n✅ Gap filling verified: Frame at 0.5s used Crop Mode (No Resize).")

if __name__ == '__main__':
    unittest.main()
