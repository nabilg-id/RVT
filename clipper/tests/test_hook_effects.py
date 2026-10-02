import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

# Efek visual butuh moviepy dan OpenCV. Lewati bila belum terpasang supaya
# suite inti tetap hijau tanpa stack media.
ColorClip = pytest.importorskip("moviepy.editor", reason="moviepy 1.x belum terpasang").ColorClip
pytest.importorskip("cv2", reason="opencv-python belum terpasang")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ..services.video_processor import VideoProcessor


class TestHookEffects(unittest.TestCase):
    def setUp(self):
        # Patch __init__ to avoid loading heavy models
        with patch.object(VideoProcessor, '__init__', return_value=None):
            self.processor = VideoProcessor()
            # Manually set up any required attributes if needed
            self.processor.caption_maker = MagicMock()
            self.processor.face_tracker = MagicMock()
            self.processor.caption_maker.styles = {'clean_white': {'name': 'Clean White'}}
            self.processor.caption_maker.selected_style = 'clean_white'

    @patch('moviepy.editor.vfx.colorx')
    @patch('moviepy.editor.vfx.lum_contrast')
    def test_apply_visual_effects(self, mock_contrast, mock_colorx):
        # Mock side effects to return the clip itself for chaining
        mock_colorx.side_effect = lambda c, x: c
        mock_contrast.side_effect = lambda c, l, co: c
        
        # Create a dummy 3-second red clip
        clip = ColorClip(size=(1080, 1920), color=(255, 0, 0), duration=3)
        
        # Apply effects
        processed_clip = self.processor.apply_visual_effects(clip)
        
        # Check if duration is preserved
        self.assertEqual(processed_clip.duration, 3)
        
        # Check if size is preserved (since we crop back)
        self.assertEqual(processed_clip.size, (1080, 1920))
        
        print("\n✅ Visual effects applied successfully without errors.")

if __name__ == '__main__':
    unittest.main()
