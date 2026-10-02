import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import OUTPUT_DIR, TEMP_DIR
from .styles.caption_styles import CAPTION_STYLES

# VideoProcessor diimpor di dalam main() dengan sengaja. Modul itu menarik
# moviepy, Whisper, dan OpenCV; mengimpornya di level modul membuat
# ``import clipper.main`` gagal di mesin yang belum memasang stack media,
# padahal validasi URL dan gaya caption tidak butuh satu pun dari itu.

# Ensure output is UTF-8 encoded and not buffered on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

# Function to print with immediate flush
def log(message):
    """Prints a message to the console with immediate flushing."""
    print(message, flush=True)

def display_caption_styles():
    """Displays the available caption styles to the user."""
    styles = CAPTION_STYLES
    log("\n🎨 Available Caption Styles (No Strokes):")
    log("=" * 50)
    for i, (key, value) in enumerate(styles.items(), 1):
        log(f"{i}. {key} - {value['name']}")
    log("=" * 50)
    return list(styles.keys())

def validate_youtube_url(url):
    """
    Validates that the provided URL is a valid YouTube HTTP(S) URL.

    The scheme is restricted to http/https on purpose. Previously any scheme
    with a youtube.com host passed, so an ``ftp://`` link would reach yt-dlp.

    Args:
        url (str): The URL to validate.

    Returns:
        bool: True if the URL is a valid YouTube URL, False otherwise.
    """
    if not url or not isinstance(url, str):
        return False

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    if not parsed.netloc:
        return False

    host = parsed.netloc.lower()
    return host == 'youtu.be' or host.endswith('youtube.com')

def main(url=None):
    """
    The main function of the YouTube Viral Clipper application.

    Args:
        url (str, optional): The YouTube URL to process. Defaults to None.
    """
    log("🚀 === YOUTUBE VIRAL CLIPPER === 🚀")
    log("✨ Features: AI Clip Selection | Smart Face Tracking | Word-by-Word Captions")
    log("")

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Ensure temp directory exists
    os.makedirs(TEMP_DIR, exist_ok=True)
    log(f"📁 Output directory: {OUTPUT_DIR}")
    log(f"📁 Temp directory: {TEMP_DIR}")

    if not url:
        url = input("🔗 Enter YouTube URL: ").strip()
    if not url:
        log("❌ Error: No URL provided.")
        return
        
    # Validate URL
    if not validate_youtube_url(url):
        log("❌ Error: Invalid YouTube URL. Please provide a valid YouTube URL.")
        return

    try:
        num_clips = int(input("📊 Number of viral clips [3]: ") or "3")
        max_duration = int(input("⏱️  Max seconds per clip [60]: ") or "60")
        min_duration = int(input("⏱️  Min seconds per clip [20]: ") or "20")

        if min_duration >= max_duration:
            log("❌ Error: Min duration must be less than max duration.")
            return

        style_keys = display_caption_styles()
        style_choice = input(f"🎨 Select caption style (1-{len(style_keys)}) [1]: ").strip() or "1"

        try:
            style_index = int(style_choice) - 1
            if 0 <= style_index < len(style_keys):
                selected_style = style_keys[style_index]
            else:
                selected_style = 'clean_white'
                log("⚠️  Invalid choice, using default: Clean White")
        except ValueError:
            selected_style = 'clean_white'
            log("⚠️  Invalid input, using default: Clean White")

        log("\n" + "="*70)
        log("🎬 STARTING VIDEO PROCESSING...")
        log("="*70)

        log("⏳ Initializing video processor...")
        from .services.video_processor import VideoProcessor

        processor = VideoProcessor(caption_style=selected_style)
        log("✅ Video processor initialized")
        
        # Add a small delay to ensure logs are displayed
        time.sleep(0.5)
        
        outputs, title = processor.process_video(url, num_clips, min_duration, max_duration)

        log("\n" + "="*70)
        log("🎉 VIRAL CLIPS GENERATED!")
        log("="*70)
        log(f"📹 Source: '{title}'")
        log(f"📁 Location: {OUTPUT_DIR.resolve()}")
        log(f"🎨 Caption Style: {processor.caption_maker.styles[selected_style]['name']}")

        total_size = 0
        log("\n📋 Generated Clips:")
        for f in outputs:
            path = Path(f)
            try:
                if path.exists():
                    size_bytes = path.stat().st_size
                    size_mb = size_bytes / (1024 * 1024)
                    total_size += size_mb
                    log(f"  - {path.name} ({size_mb:.2f} MB)")
            except Exception as e:
                log(f"  - {path.name} (size unknown: {e})")
        log(f"\n💾 Total Size: {total_size:.2f} MB")
        log("\n✅ Done! Enjoy your viral clips!")

    except ValueError as e:
        log(f"\n❌ Input Error: {e}")
    except FileNotFoundError as e:
        log(f"\n❌ File Error: {e}")
    except Exception as e:
        log(f"\n❌ An unexpected error occurred: {e}")
        import traceback
        traceback.print_exc()
        log("\nPlease check the error message above and try again.")

if __name__ == "__main__":
    try:
        # Check if URL was provided as command-line argument
        url = None
        if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
            url = sys.argv[1].strip()
            # Basic URL validation
            if not url.startswith('http'):
                url = 'https://' + url
        elif len(sys.argv) > 2 and sys.argv[1] in ("--web", "--gui"):
            from .app import run_server
            run_server()
            sys.exit(0)

        # Directories are already created at import time by config, resolved
        # from the package location rather than the working directory.
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)

        main(url)
    except KeyboardInterrupt:
        print("\n\n🛑 Process interrupted by user. Exiting gracefully.")
    except Exception as e:
        print(f"\n\n❌ Critical error: {e}")
        print("Please report this issue if it persists.")
