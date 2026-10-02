from pathlib import Path

from moviepy.editor import VideoFileClip, concatenate_videoclips, vfx

from ..config import OUTPUT_DIR, TEMP_DIR, TRANSITION_FILE
from ..services.ai_selector import AISelector
from ..services.caption_maker import CaptionMaker
from ..services.face_tracker import FaceTracker
from ..services.whisper_transcriber import WhisperSingleton
from ..services.youtube_downloader import YouTubeDownloader
from ..utils.helpers import cleanup_temp_files, generate_random_clips


class VideoProcessor:
    """
    Orchestrates the entire video processing pipeline.

    This class initializes and manages all the services required for
    downloading, transcribing, selecting clips, tracking faces, and
    adding captions to a YouTube video.
    """
    def __init__(self, caption_style='clean_white'):
        """
        Initializes the VideoProcessor with a specific caption style.

        Args:
            caption_style (str, optional): The style of captions to use.
                                            Defaults to 'clean_white'.
        """
        self.downloader = YouTubeDownloader()
        self.transcriber = WhisperSingleton()
        self.ai_selector = AISelector()
        self.face_tracker = FaceTracker()
        self.caption_maker = CaptionMaker(caption_style)

    def apply_visual_effects(self, clip):
        """
        Applies visual effects (Zoom, Saturation, Contrast) to a clip.
        """
        # 1. Color Boost (Saturation & Contrast)
        clip = clip.fx(vfx.colorx, 1.3)  # 30% more saturation
        clip = clip.fx(vfx.lum_contrast, 0, 0.3)  # 30% more contrast

        # 2. Zoom in 10%, then center-crop back to the original frame.
        #
        # A frame-by-frame dynamic zoom was tried here first and abandoned: it
        # needed a per-frame PIL resize inside moviepy's own get_frame, which
        # was choppy and could not be made reliable. It also referenced numpy
        # without importing it. The constant "pop" below is what actually runs.
        w, h = clip.size

        clip = clip.resize(1.1) # Zoom in 10%
        # Center crop back to original resolution
        clip = clip.crop(x_center=clip.w/2, y_center=clip.h/2, width=w, height=h)
        
        return clip

    def process_video(self, url, num_clips, min_duration, max_duration):
        """
        Processes a YouTube video to generate viral clips.

        Args:
            url (str): The URL of the YouTube video.
            num_clips (int): The number of clips to generate.
            min_duration (int): The minimum duration of each clip.
            max_duration (int): The maximum duration of each clip.

        Returns:
            tuple: A tuple containing a list of output file paths and the
                   title of the video.
        """
        print("📥 Downloading video...")
        video_path, title, duration = self.downloader.download(url)
        print(f"✅ Download complete: {title} ({duration:.1f}s)")

        print("🎵 Starting transcription with Whisper...")
        words, transcript, segments = self.transcriber.transcribe(video_path)
        print("✅ Transcription processing complete")

        if not segments:
            print("❌ Transcription failed. Using random clips.")
            print("🎲 Generating random clips...")
            clip_specs = generate_random_clips(duration, num_clips, min_duration, max_duration)
            print(f"✅ Generated {len(clip_specs)} random clips")
        else:
            print("🧠 Using AI to select the most viral clips...")
            clip_specs = self.ai_selector.select_clips(
                segments, duration, num_clips, min_duration, max_duration
            )
            print(f"✅ AI selected {len(clip_specs)} viral clips")

        if not clip_specs:
            raise ValueError("Could not select any clips from the video.")

        output_files = []
        print(f"\n🎬 Processing {len(clip_specs)} viral clips...")

        for i, clip_info in enumerate(clip_specs, 1):
            start = clip_info['start']
            end = clip_info['end']
            title_text = clip_info.get('title', f'Clip {i}')
            virality_score = clip_info.get('virality_score', 0)
            
            # Hook data
            hook_data = clip_info.get('hook_segment', {})
            hook_start = hook_data.get('start', start)
            hook_end = hook_data.get('end', min(start + 3, end))

            print(f"\n📹 Clip {i}/{len(clip_specs)}: {title_text}")
            print(f"    ⭐ Virality Score: {virality_score}/100")
            print(f"    ⏱️  Time: {start:.1f}s to {end:.1f}s")
            print(f"    🎣 Hook: {hook_start:.1f}s to {hook_end:.1f}s")
            print(f"    🎨 Caption Style: {self.caption_maker.styles[self.caption_maker.selected_style]['name']}")

            try:
                with VideoFileClip(str(video_path)) as video:
                    # Check for hook overlap
                    main_start = start
                    if abs(hook_start - start) < 1.0:
                        print(f"    ✂️ Hook overlaps with start, trimming main clip start to {hook_end:.1f}s")
                        main_start = hook_end

                    # 1. Main Clip
                    # Ensure main_start is not beyond end
                    if main_start >= end:
                        print("    ⚠️ Main clip trimmed entirely (Hook covers all). Setting min length.")
                        main_start = max(start, end - 5.0) # Fallback to last 5s? Or just keep original?
                        # Actually if hook covers all, maybe we just want the hook? 
                        # But user wants [Hook] -> [Transition] -> [Main]. 
                        # If Main is same as Hook, then we have [Hook] -> [Transition] -> [Hook] (redundant).
                        # Let's assume if it overlaps completely, we just show the hook and maybe a bit more if possible.
                        # For now, let's just ensure main_start < end.
                    
                    main_clip = video.subclip(main_start, end)
                    
                    # 2. Hook Clip
                    hook_clip = video.subclip(hook_start, hook_end)
                    
                    print("    🎯 Applying intelligent face tracking to Main Clip...")
                    main_clip = self.face_tracker.track_and_crop(main_clip)
                    
                    print("    🎯 Applying intelligent face tracking to Hook Clip...")
                    hook_clip = self.face_tracker.track_and_crop(hook_clip)
                    
                    width, height = main_clip.size
                    
                    # 3. Apply Visual Effects to Hook
                    print("    ✨ Applying visual effects to Hook...")
                    hook_clip = self.apply_visual_effects(hook_clip)
                    
                    # 4. Add Captions
                    if words:
                        print("    📝 Adding captions to Main Clip...")
                        main_clip = self.caption_maker.add_captions(main_clip, words, main_start)
                        
                        print("    📝 Adding captions to Hook Clip...")
                        hook_clip = self.caption_maker.add_captions(hook_clip, words, hook_start)

                    # 5. Transition Clip
                    transition_path = TRANSITION_FILE
                    clips_to_concat = [hook_clip]
                    
                    if transition_path.exists():
                        print("    🔄 Inserting transition video...")
                        transition_clip = VideoFileClip(str(transition_path))
                        # Resize transition to match main clip
                        transition_clip = transition_clip.resize(newsize=(width, height))
                        clips_to_concat.append(transition_clip)
                    else:
                        print(f"    ⚠️ Transition video not found at {transition_path}, skipping.")

                    clips_to_concat.append(main_clip)

                    # 6. Concatenate
                    print("    🔗 Merging Hook + Transition + Main Clip...")
                    final_clip = concatenate_videoclips(clips_to_concat, method="compose")

                    filename = f"clip_{i}_{virality_score}pts_{Path(video_path).stem}.mp4"
                    output_path = OUTPUT_DIR / filename

                    print("    🎥 Encoding with optimized settings...")
                    print("    ⏳ Starting video encoding (this may take a while)...")
                    final_clip.write_videofile(
                        str(output_path),
                        codec='libx264',
                        audio_codec='aac',
                        # Use 'faster' preset for better performance with minimal quality loss
                        preset='faster',
                        # Use slightly higher CRF (20) for better compression with minimal quality loss
                        ffmpeg_params=['-crf', '20', '-pix_fmt', 'yuv420p', '-threads', '2'],
                        verbose=True,  # Enable verbose output to show progress
                        logger=None,
                        temp_audiofile=str(TEMP_DIR / f'temp_audio_{i}.m4a'),
                        remove_temp=True,
                        # Use 2 threads for encoding to avoid overloading the system
                        threads=2
                    )
                    print("    ✅ Video encoding complete")
                    output_files.append(str(output_path))
                    print(f"    ✅ Saved: {filename}")

            except Exception as e:
                print(f"    ❌ Error processing clip {i}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        self.face_tracker.close()
        cleanup_temp_files()

        return output_files, title
