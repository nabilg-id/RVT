import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
import yt_dlp

from ..config import (
    COOKIES_FILE,
    TEMP_DIR,
    YOUTUBE_COOKIES_BROWSER,
    YOUTUBE_COOKIES_CONTENT,
    YOUTUBE_USER_AGENT,
)


class YouTubeDownloader:
    def __init__(self, temp_dir=TEMP_DIR):
        self.temp_dir = Path(temp_dir)

    @staticmethod
    def get_video_id(url):
        if 'youtu.be' in url:
            return url.split('/')[-1].split('?')[0]
        if 'youtube.com' in url:
            return parse_qs(urlparse(url).query).get('v', [None])[0]
        return None

    def _get_js_runtimes(self):
        """Detect available JavaScript runtime for yt-dlp to solve YouTube JS challenges."""
        runtimes = {}
        for rt in ('node', 'deno', 'quickjs', 'bun'):
            if shutil.which(rt):
                runtimes[rt] = {}
                break
        return runtimes

    def download(self, url):
        video_id = self.get_video_id(url)
        if not video_id:
            raise ValueError("Invalid YouTube URL provided.")
        
        # Ensure temp directory exists
        os.makedirs(self.temp_dir, exist_ok=True)
        
        # Get path to ffmpeg executable from imageio-ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"🎬 Using FFmpeg from: {ffmpeg_path}")

        js_runtimes = self._get_js_runtimes()
        if js_runtimes:
            print(f"⚡ JS runtime detected for yt-dlp: {list(js_runtimes.keys())[0]}")

        opts = {
            'format': 'bestvideo*+bestaudio/best',
            'outtmpl': str(self.temp_dir / f'{video_id}.%(ext)s'),
            'quiet': False,
            'merge_output_format': 'mp4',
            'geo_bypass': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': False,
            'retries': 10,
            'fragment_retries': 10,
            'extractor_retries': 10,
            'ffmpeg_location': ffmpeg_path,
        }

        if js_runtimes:
            opts['js_runtimes'] = js_runtimes

        if YOUTUBE_USER_AGENT:
            opts['user_agent'] = YOUTUBE_USER_AGENT

        # Cookies handling:
        # 1. Check the cookies file resolved from config, not the CWD, so the
        #    GUI and CLI find the same file regardless of where they are run.
        # 2. Check YOUTUBE_COOKIES_CONTENT from env
        # 3. Check browser cookies
        cookie_file_path = None
        local_cookies_file = COOKIES_FILE
        if local_cookies_file.exists() and local_cookies_file.stat().st_size > 0:
            print(f"Authentication cookies found from file: {local_cookies_file.name}")
            opts['cookiefile'] = str(local_cookies_file.resolve())
        elif YOUTUBE_COOKIES_CONTENT and "PASTE" not in YOUTUBE_COOKIES_CONTENT:
            print("Authentication cookies found from environment. Applying to request.")
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, dir=self.temp_dir, suffix='.txt') as cookie_file:
                cookie_file.write(YOUTUBE_COOKIES_CONTENT)
                cookie_file_path = cookie_file.name
            opts['cookiefile'] = cookie_file_path
        elif YOUTUBE_COOKIES_BROWSER:
            print(f"Using cookies from browser: {YOUTUBE_COOKIES_BROWSER}")
            opts['cookiesfrombrowser'] = (YOUTUBE_COOKIES_BROWSER,)
        else:
            print("Notice: No cookies provided. If YouTube blocks downloads, you can export cookies to cookies.txt")

        try:
            print("📥 Downloading video with yt-dlp (Best Quality)...")
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                except yt_dlp.utils.DownloadError as e:
                    print(f"First download attempt failed: {str(e)}")
                    print("Trying alternative download method with player_client fallback...")
                    # Fallback 1: Try with TV/Web/Android player clients
                    fallback_opts = opts.copy()
                    fallback_opts['extractor_args'] = {
                        'youtube': {'player_client': ['tv', 'web', 'android', 'ios']}
                    }
                    fallback_opts['format'] = 'bestvideo+bestaudio/best[ext=mp4]/best'
                    try:
                        with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                            info = ydl2.extract_info(url, download=True)
                    except yt_dlp.utils.DownloadError as e2:
                        print(f"Second download attempt failed: {str(e2)}")
                        print("Trying fallback format best/bestvideo*+bestaudio...")
                        # Fallback 2: General best stream
                        fallback_opts2 = opts.copy()
                        fallback_opts2['format'] = 'best/bestvideo*+bestaudio'
                        with yt_dlp.YoutubeDL(fallback_opts2) as ydl3:
                            info = ydl3.extract_info(url, download=True)
        except Exception as e:
            raise Exception(f"Failed to download video: {str(e)}")
        finally:
            if cookie_file_path and os.path.exists(cookie_file_path):
                try:
                    os.remove(cookie_file_path)
                except OSError:
                    pass

        video_path = next(self.temp_dir.glob(f'{video_id}.mp4'), None)
        if not video_path:
            # Try to find any video file with the video_id prefix
            video_path = next(self.temp_dir.glob(f'{video_id}.*'), None)
            if not video_path:
                raise FileNotFoundError("Failed to download the video file.")

        return video_path, info.get('title', 'N/A'), info.get('duration', 0)

