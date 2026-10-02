CAPTION_STYLES = {
    'clean_white': {
        'text_color': (255, 255, 255, 255),
        'stroke_width': 2,
        'stroke_color': (0, 0, 0, 255),
        'shadow_offset': (2, 2),
        'shadow_color': (0, 0, 0, 100),
        'font_type': 'bold',
        'font_scale': 1.0,
        'uppercase': False,
        'name': 'Clean White'
    },
    'viral_yellow': {  # Alex Hormozi Style
        'text_color': (255, 255, 0, 255),
        'stroke_width': 4,
        'stroke_color': (0, 0, 0, 255),
        'shadow_offset': (4, 4),
        'shadow_color': (0, 0, 0, 200),
        'font_type': 'bold',
        'font_scale': 1.2,
        'uppercase': True,
        'name': 'Viral Yellow (Hormozi)'
    },
    'viral_green': {
        'text_color': (0, 255, 0, 255),
        'stroke_width': 4,
        'stroke_color': (0, 0, 0, 255),
        'shadow_offset': (4, 4),
        'shadow_color': (0, 0, 0, 200),
        'font_type': 'bold',
        'font_scale': 1.2,
        'uppercase': True,
        'name': 'Viral Green'
    },
    'viral_red': {
        'text_color': (255, 0, 0, 255),
        'stroke_width': 4,
        'stroke_color': (0, 0, 0, 255),
        'shadow_offset': (4, 4),
        'shadow_color': (0, 0, 0, 200),
        'font_type': 'bold',
        'font_scale': 1.2,
        'uppercase': True,
        'name': 'Viral Red'
    },
    'neon_cyan': {
        'text_color': (0, 255, 255, 255),
        'stroke_width': 2,
        'stroke_color': (0, 0, 0, 255),
        'shadow_offset': (6, 6),
        'shadow_color': (0, 100, 100, 150),  # Glow effect mimic
        'font_type': 'bold',
        'font_scale': 1.1,
        'uppercase': True,
        'name': 'Neon Cyan'
    },
    'neon_pink': {
        'text_color': (255, 20, 147, 255),
        'stroke_width': 2,
        'stroke_color': (255, 255, 255, 255),
        'shadow_offset': (4, 4),
        'shadow_color': (255, 0, 100, 100),
        'font_type': 'bold',
        'font_scale': 1.1,
        'uppercase': True,
        'name': 'Neon Pink'
    },
     'bold_black_bg': {
        'text_color': (255, 255, 255, 255),  # White Text
        'stroke_width': 0,
        'stroke_color': None,
        'shadow_offset': (0, 0),
        'shadow_color': None,
        'bg_color': (0, 0, 0, 200), # SEm-transparent black box
        'font_type': 'bold',
        'font_scale': 1.0,
        'uppercase': True, 
        'name': 'Bold Black BG'
    }
}

HIGHLIGHT_KEYWORDS = [
    'amazing', 'incredible', 'secret', 'important', 'shocking', 'exclusive',
    'never', 'always', 'only', 'must', 'can\'t', 'won\'t', 'best', 'worst',
    'first', 'last', 'biggest', 'smallest', 'most', 'least', 'why', 'how',
    'what', 'when', 'where', 'money', 'free', 'easy', 'hard', 'truth'
]
