import sys
from pathlib import Path

import pytest

# Modul caption butuh stack media (Pillow, moviepy). Lewati bila belum
# terpasang supaya suite inti tetap hijau di mesin tanpa dependensi berat.
Image = pytest.importorskip("PIL.Image", reason="Pillow belum terpasang")
pytest.importorskip("moviepy.editor", reason="moviepy 1.x belum terpasang")

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from ..services.caption_maker import CaptionMaker
from ..styles.caption_styles import CAPTION_STYLES


def test_styles():
    output_dir = Path('tests/output')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    video_size = (1080, 1920) # 9:16 aspect ratio
    base_font_size = 65
    
    print(f"Generating test images in {output_dir}...")
    
    for style_name in CAPTION_STYLES.keys():
        print(f"Testing style: {style_name}")
        
        try:
            maker = CaptionMaker(selected_style=style_name)
            
            # Test Normal Word
            img_normal = maker.create_word_image(
                "Normal", 
                video_size, 
                base_font_size, 
                is_highlighted=False
            )
            
            # Test Highlighted Word
            img_highlight = maker.create_word_image(
                "HIGHLIGHT", 
                video_size, 
                base_font_size, 
                is_highlighted=True
            )
            
            # Save normal
            im_n = Image.fromarray(img_normal)
            # Add a background to see white text
            bg_n = Image.new('RGB', video_size, (50, 50, 50))
            bg_n.paste(im_n, (0, 0), im_n)
            bg_n.save(output_dir / f"{style_name}_normal.png")
            
            # Save highlight
            im_h = Image.fromarray(img_highlight)
            bg_h = Image.new('RGB', video_size, (50, 50, 50))
            bg_h.paste(im_h, (0, 0), im_h)
            bg_h.save(output_dir / f"{style_name}_highlight.png")
            
        except Exception as e:
            print(f"❌ Failed to generate {style_name}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    test_styles()
