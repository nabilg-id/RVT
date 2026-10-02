from pathlib import Path

import numpy as np
from moviepy.editor import CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont

from ..styles.caption_styles import CAPTION_STYLES, HIGHLIGHT_KEYWORDS


class CaptionMaker:
    """
    Creates and adds word-by-word captions to a video clip.
    """
    def __init__(self, selected_style='clean_white'):
        """
        Initializes the CaptionMaker with a selected caption style.

        Args:
            selected_style (str, optional): The style of captions to use.
                                            Defaults to 'clean_white'.
        """
        self.font_paths = self.find_available_fonts()
        self.selected_style = selected_style
        self.styles = CAPTION_STYLES
        self.highlight_keywords = HIGHLIGHT_KEYWORDS

    def find_available_fonts(self):
        """
        Finds available fonts on the system.

        Returns:
            dict: A dictionary of available font paths.
        """
        font_collections = {
            'bold': [
                'C:/Windows/Fonts/impact.ttf',
                'C:/Windows/Fonts/ariblk.ttf',
                'C:/Windows/Fonts/arialbd.ttf',
                'C:/Windows/Fonts/calibrib.ttf',
                'C:/Windows/Fonts/verdanab.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                '/System/Library/Fonts/Helvetica.ttc',
                '/System/Library/Fonts/Arial.ttf',
                '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf'
            ],
            'regular': [
                'C:/Windows/Fonts/arial.ttf',
                'C:/Windows/Fonts/calibri.ttf',
                'C:/Windows/Fonts/verdana.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                '/System/Library/Fonts/Helvetica.ttc',
                '/usr/share/fonts/TTF/DejaVuSans.ttf'
            ]
        }

        found_fonts = {'bold': None, 'regular': None}

        for font_type, paths in font_collections.items():
            for path in paths:
                if Path(path).exists():
                    found_fonts[font_type] = path
                    print(f"    📝 Found {font_type} font: {Path(path).name}")
                    break

        if not found_fonts['bold'] and not found_fonts['regular']:
            print("    📝 Using default system fonts")

        return found_fonts

    def get_font(self, font_type, font_size):
        """
        Gets a font object for a given type and size.

        Args:
            font_type (str): The type of font (e.g., 'bold', 'regular').
            font_size (int): The size of the font.

        Returns:
            ImageFont: An ImageFont object.
        """
        try:
            font_path = self.font_paths.get(font_type)
            if font_path:
                return ImageFont.truetype(font_path, font_size)
            else:
                return ImageFont.load_default()
        except (OSError, ValueError):
            # Font file missing or unreadable; fall back to Pillow's built-in.
            return ImageFont.load_default()

    def create_word_image(self, word, video_size, base_font_size, is_highlighted=False):
        """
        Creates an image of a single word with advanced styling (stroke, shadow, etc.).
        """
        width, height = video_size

        # Get style configuration
        style_config = self.styles.get(self.selected_style, self.styles['clean_white'])
        
        # 1. Handle Uppercase
        if style_config.get('uppercase', False):
            word = word.upper()

        # 2. Handle Font Scaling
        # Default scale is 1.0 if not specified
        font_scale = style_config.get('font_scale', 1.0)
        final_font_size = int(base_font_size * font_scale)
        
        font_type = style_config['font_type']
        font = self.get_font(font_type, final_font_size)

        # Create image with transparent background
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Calculate text position
        temp_bbox = draw.textbbox((0, 0), word, font=font)
        text_width = temp_bbox[2] - temp_bbox[0]
        text_height = temp_bbox[3] - temp_bbox[1]

        x = (width - text_width) // 2
        
        # Position logic
        safe_bottom_margin = int(height * 0.15)
        y = height - text_height - safe_bottom_margin
        
        # Ensure it doesn't go too high (keep it in lower third roughly, but adjustable)
        # Viral captions often sit a bit higher than standard subtitles to avoid UI elements
        # Let's align slightly above bottom third
        if y < height * 0.6: 
             y = int(height * 0.6)

        # 3. Handle Background Color (e.g. for 'Bold Black BG' style)
        bg_color = style_config.get('bg_color')
        if bg_color:
            padding = 10
            bg_bbox = [
                x - padding, 
                y - padding, 
                x + text_width + padding, 
                y + text_height + padding
            ]
            draw.rectangle(bg_bbox, fill=bg_color)

        # 4. Handle Shadow
        shadow_offset = style_config.get('shadow_offset', (0, 0))
        shadow_color = style_config.get('shadow_color')
        
        if shadow_color and (shadow_offset[0] != 0 or shadow_offset[1] != 0):
            # Draw shadow multiple times for softer/thicker look if needed, 
            # but simple offset is usually enough for "hard" viral shadows
            draw.text(
                (x + shadow_offset[0], y + shadow_offset[1]), 
                word, 
                font=font, 
                fill=shadow_color
            )

        # 5. Handle Stroke (Outline)
        stroke_width = style_config.get('stroke_width', 0)
        stroke_color = style_config.get('stroke_color')
        
        if is_highlighted:
             # Highlight override: Bright Yellow Text, Black Stroke usually
             text_color = (255, 255, 0, 255)
             # Ensure visible stroke for highlighted text if not already set high
             if stroke_width < 2:
                 stroke_width = 4
                 stroke_color = (0, 0, 0, 255)
        else:
             text_color = style_config['text_color']

        # Draw text with stroke if customized
        if stroke_width > 0 and stroke_color:
             draw.text(
                (x, y), 
                word, 
                font=font, 
                fill=text_color, 
                stroke_width=stroke_width, 
                stroke_fill=stroke_color
            )
        else:
            # Standard draw without stroke
            draw.text((x, y), word, font=font, fill=text_color)

        return np.array(img)

    def add_captions(self, clip, words, clip_start_time):
        """
        Adds word-by-word captions to a video clip.

        Args:
            clip (moviepy.editor.VideoFileClip): The video clip to add captions to.
            words (list): A list of words with timestamps.
            clip_start_time (float): The start time of the clip in the original
                                     video.

        Returns:
            moviepy.editor.VideoFileClip: The video clip with captions.
        """
        if not words:
            return clip

        caption_clips = []
        clip_words = []

        for word in words:
            word_start = word['start']
            word_end = word['end']
            word_text = word['word'].strip()

            relative_start = word_start - clip_start_time
            relative_end = word_end - clip_start_time

            if relative_end <= 0 or relative_start >= clip.duration:
                continue

            relative_start = max(0, relative_start)
            relative_end = min(clip.duration, relative_end)
            duration = relative_end - relative_start

            if duration <= 0.05:
                continue

            clip_words.append({
                'word': word_text.upper(),
                'start': relative_start,
                'end': relative_end,
                'duration': duration
            })

        if not clip_words:
            return clip

        video_width, video_height = clip.size
        base_font_size = max(48, int(min(video_width, video_height) * 0.06))

        for word_data in clip_words:
            word_text = word_data['word']
            start_time = word_data['start']
            duration = word_data['duration']

            is_highlighted = any(keyword.upper() in word_text for keyword in self.highlight_keywords)

            word_img = self.create_word_image(word_text, (video_width, video_height), base_font_size, is_highlighted)

            word_clip = ImageClip(word_img, duration=duration, transparent=True)
            word_clip = word_clip.set_start(start_time).fadein(0.1).fadeout(0.1)
            caption_clips.append(word_clip)

        if caption_clips:
            return CompositeVideoClip([clip] + caption_clips)
        return clip
