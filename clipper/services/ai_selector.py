import json
import random

from openai import OpenAI

from ..config import OPENROUTER_API_KEY, OPENROUTER_MODEL


class AISelector:
    """
    Uses the OpenRouter API (OpenAI client) to select the most viral clips from a transcript.
    """
    def __init__(self):
        """
        Initializes the AISelector with OpenRouter configuration.
        """
        # config.py already strips whitespace, but a key made only of spaces is
        # truthy: without this check it would be sent as a bearer token and come
        # back as an opaque 401 instead of the clear message below.
        #
        # This message is what a new user sees in the GUI when Generate fails,
        # and on a fresh install it is the single most likely first failure, so
        # it carries the whole remedy: which variable, which file, and where to
        # get a key. Naming the variable alone is not enough - neither the
        # filename ".env" nor the fact that the key is free at openrouter.ai is
        # guessable from the name.
        if not (OPENROUTER_API_KEY or "").strip():
            raise ValueError(
                "OPENROUTER_API_KEY belum diisi. Buatkan file .env di folder "
                "project (salin clipper/.env.example), lalu isi "
                "OPENROUTER_API_KEY=... Ambil key gratis di "
                "https://openrouter.ai/keys. Tanpa key ini, fitur Generate "
                "tidak bisa berjalan; fitur unduh YouTube tetap normal."
            )

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=(OPENROUTER_API_KEY or "").strip(),
        )
        self.model = OPENROUTER_MODEL
        print(f"🤖 Initialized AI Selector with model: {self.model}")

    def select_clips(self, segments, video_duration, n, min_dur, max_dur):
        """
        Selects the most viral clips from a transcript using the AI model.

        Args:
            segments (list): A list of transcript segments with timestamps.
            video_duration (float): The total duration of the video.
            n (int): The number of clips to select.
            min_dur (int): The minimum duration of each clip.
            max_dur (int): The maximum duration of each clip.

        Returns:
            list: A list of dictionaries, each representing a selected clip.
        """
        segments_text = []
        for i, seg in enumerate(segments):
            segments_text.append(f"[{seg['start']:.1f}s-{seg['end']:.1f}s]: {seg['text']}")
        
        transcript_with_timestamps = "\n".join(segments_text)
        
        system_prompt = "You are an expert at creating viral short-form content like Opus.pro."
        
        user_prompt = f"""Analyze this transcript with precise timestamps and select the {n} BEST viral clips.

CRITICAL RULES:
1. Each clip MUST start at the EXACT beginning of a sentence/thought and end at the EXACT completion of that sentence/thought
2. Never cut off mid-sentence or mid-word - clips must be complete thoughts
3. Each clip must be {min_dur}-{max_dur} seconds long
4. Clips cannot overlap and must use the EXACT timestamps provided
6. IDENTIFY A HOOK: For each clip, identify the MOST engaging "complete thought" or sentence to be used as a teaser.
   - It does NOT have to be exactly 3 seconds.
   - It MUST be a complete sentence or phrase.
   - It can be shorter or longer than 3s, as long as it packs a punch and feels complete.

SELECTION CRITERIA (prioritize):
- Complete engaging stories or thoughts
- Surprising facts or revelations 
- Actionable advice or tips
- Emotional moments or reactions
- Quotable one-liners with context
- Question-answer pairs

VIDEO DURATION: {video_duration} seconds

TRANSCRIPT WITH EXACT TIMESTAMPS:
{transcript_with_timestamps}

Return ONLY valid JSON with EXACT timestamps from the transcript:
{{
  "clips": [
    {{
      "start": 34.5,
      "end": 67.2,
      "title": "Complete thought or hook",
      "virality_score": 85,
      "hook_type": "story_reveal",
      "reason": "Complete engaging story with clear beginning and end",
      "hook_segment": {{
        "start": 34.5,
        "end": 37.5
      }}
    }}
  ]
}}"""
        
        try:
            print(f"🤖 AI ({self.model}) analyzing transcript for complete viral thoughts...")

            data = self._ask_model(system_prompt, user_prompt)

            validated_clips = []

            for clip_data in data.get('clips', []):
                start = clip_data.get('start')
                end = clip_data.get('end')
                title = clip_data.get('title', 'Untitled')
                score = clip_data.get('virality_score', 0)
                hook_type = clip_data.get('hook_type', 'general')

                if start is None or end is None:
                    continue

                start, end = float(start), float(end)
                duration = end - start

                if duration > max_dur:
                    end = start + max_dur
                    duration = max_dur

                if min_dur <= duration <= max_dur and start < end and end <= video_duration:
                    validated_clips.append({
                        'start': start,
                        'end': end,
                        'title': title,
                        'virality_score': score,
                        'hook_type': hook_type,
                        'duration': duration,
                        'hook_segment': {
                            'start': clip_data.get('hook_segment', {}).get('start', start),
                            'end': clip_data.get('hook_segment', {}).get('end', start + 3)
                        }
                    })

            if not validated_clips:
                # Salvage before giving up. Discarding a whole answer because
                # one clip came back 12s against an 8s ceiling throws away the
                # model's actual choice; clamping keeps the moment it picked.
                validated_clips = self._salvage(
                    data.get('clips', []), video_duration, min_dur, max_dur
                )
                if validated_clips:
                    print(f"⚠️ {len(validated_clips)} clip AI disesuaikan agar "
                          "sesuai batas durasi.")

            if not validated_clips:
                raise ValueError("AI did not return any valid clips.")

            validated_clips.sort(key=lambda x: x['virality_score'], reverse=True)
            print(f"✅ AI selected {len(validated_clips)} complete viral clips:")
            for i, clip in enumerate(validated_clips[:n], 1):
                print(f"  {i}. {clip['title']} (Score: {clip['virality_score']}, Type: {clip['hook_type']})")

            return validated_clips[:n]

        except Exception as e:
            print(f"❌ AI clip selection failed: {e}. Using fallback method.")
            return self._fallback_selection(segments, video_duration, n, min_dur, max_dur)

    #: Extra attempts when the model returns an empty or unparsable body.
    #: openrouter/free load-balances onto whatever free model is free at the
    #: time, and roughly one request in five comes back empty; measured over
    #: five runs. One retry turns that into a failure rate low enough to not be
    #: noticed.
    _MAX_ATTEMPTS = 2

    def _ask_model(self, system_prompt, user_prompt):
        """Ask the model for clips and return the parsed JSON.

        Retries once on an empty or unparsable body. A reasoning model that
        spends its whole budget thinking returns an empty ``content``, which
        surfaces as ``Expecting value: line 1 column 1 (char 0)`` - a message
        that says nothing about the real cause.
        """
        last_error = None

        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            if attempt > 1:
                print("🔄 Respons kosong, mencoba lagi...")

            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                content = completion.choices[0].message.content
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                continue

            if not content or not str(content).strip():
                last_error = ValueError("Model mengembalikan respons kosong.")
                continue

            try:
                return self._parse_clips(str(content))
            except ValueError as exc:
                last_error = exc

        raise last_error or ValueError("Model tidak memberi jawaban.")

    @staticmethod
    def _parse_clips(response_content):
        """Pull the clips object out of a model reply."""
        text = response_content.strip()

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Prose around the JSON is common; take the outermost braces.
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"JSON tidak bisa dibaca: {exc}") from exc
            raise ValueError("Respons tidak berisi JSON.")

    @staticmethod
    def _salvage(clips, video_duration, min_dur, max_dur):
        """Force the model's picks into range instead of discarding them.

        Clamps each window to the video and to the duration bounds. A clip that
        cannot be made usable - no timestamps, or degenerate after clamping -
        is dropped, but a merely over-long or out-of-bounds clip is kept with
        the model's intent preserved.
        """
        rescued = []
        for clip_data in clips or []:
            try:
                start = float(clip_data.get('start'))
                end = float(clip_data.get('end'))
            except (TypeError, ValueError):
                continue

            if end <= start:
                continue

            # Pull inside the video.
            start = max(0.0, min(start, max(0.0, video_duration - min_dur)))
            end = min(float(video_duration), max(end, start + min_dur))
            end = min(end, start + max_dur)
            end = min(end, float(video_duration))

            if end - start < min_dur:
                continue

            hook = clip_data.get('hook_segment') or {}
            try:
                hook_start = float(hook.get('start', start))
            except (TypeError, ValueError):
                hook_start = start
            try:
                hook_end = float(hook.get('end', hook_start + 3))
            except (TypeError, ValueError):
                hook_end = hook_start + 3
            hook_start = max(start, min(hook_start, end))
            hook_end = max(hook_start, min(hook_end, end))

            rescued.append({
                'start': start,
                'end': end,
                'title': clip_data.get('title', 'Untitled'),
                'virality_score': clip_data.get('virality_score', 0),
                'hook_type': clip_data.get('hook_type', 'general'),
                'duration': end - start,
                'hook_segment': {'start': hook_start, 'end': hook_end},
            })

        return rescued

    def _fallback_selection(self, segments, video_duration, n, min_dur, max_dur):
        """Pick clips without asking the model.

        Two passes. The first accumulates whole transcript segments, which gives
        the nicest boundaries when segments happen to be shorter than max_dur.
        The second slices segments that are too long on their own.

        That second pass is not optional: faster-whisper can hand back segments
        longer than max_dur - a sparse video after VAD filtering, or a short clip
        with one continuous sentence - and the original single pass could not
        fit any of them, returned an empty list, and the pipeline aborted with
        "Could not select any clips from the video" even though the video was
        perfectly usable.
        """
        if not segments:
            return self._even_windows(video_duration, n, min_dur, max_dur)

        clips = self._accumulate_segments(segments, n, min_dur, max_dur)
        if clips:
            return clips[:n]

        clips = self._slice_long_segments(segments, n, min_dur, max_dur)
        if clips:
            return clips[:n]

        return self._even_windows(video_duration, n, min_dur, max_dur)

    @staticmethod
    def _accumulate_segments(segments, n, min_dur, max_dur):
        """Grow a clip from consecutive segments until it is long enough."""
        clips = []
        used_segments = set()
        attempts = 0
        max_attempts = len(segments) * 2

        while len(clips) < n and attempts < max_attempts:
            attempts += 1

            available = [i for i in range(len(segments)) if i not in used_segments]
            if not available:
                break

            start_idx = random.choice(available)
            current_duration = 0
            end_idx = start_idx

            while end_idx < len(segments):
                seg = segments[end_idx]
                if end_idx in used_segments and end_idx != start_idx:
                    break

                segment_duration = seg['end'] - seg['start']
                if current_duration + segment_duration > max_dur:
                    break

                current_duration += segment_duration
                end_idx += 1

                if current_duration >= min_dur:
                    break

            if current_duration >= min_dur:
                for i in range(start_idx, end_idx):
                    used_segments.add(i)

                start_time = segments[start_idx]['start']
                end_time = segments[end_idx - 1]['end']
                clips.append({
                    'start': start_time,
                    'end': end_time,
                    'title': f'Fallback clip {len(clips) + 1}',
                    'virality_score': 50,
                    'hook_type': 'general',
                    'duration': end_time - start_time,
                })

        return clips

    @staticmethod
    def _slice_long_segments(segments, n, min_dur, max_dur):
        """Cut a max_dur window out of each segment that is too long alone."""
        clips = []
        window = max(min_dur, min(max_dur, max_dur))
        for seg in segments:
            if len(clips) >= n:
                break
            start = seg['start']
            length = seg['end'] - start
            if length <= max_dur:
                continue
            offset = 0.0
            while offset < length and len(clips) < n:
                end = start + min(window, length - offset)
                if end - (start + offset) < min(min_dur, length):
                    break
                clips.append({
                    'start': start + offset,
                    'end': end,
                    'title': f'Fallback clip {len(clips) + 1}',
                    'virality_score': 50,
                    'hook_type': 'general',
                    'duration': end - (start + offset),
                })
                offset += window
        return clips

    @staticmethod
    def _even_windows(video_duration, n, min_dur, max_dur):
        """Last resort: evenly spaced windows across the whole video.

        Keeps the promise that a usable video yields at least one clip, so a
        transient model failure degrades the quality of the result rather than
        failing the job.
        """
        if not video_duration or video_duration <= 0 or n <= 0:
            return []

        count = n
        span = min(max_dur, video_duration)
        if span <= 0:
            return []

        gap = max(0.0, (video_duration - span) / max(1, count))
        clips = []
        for i in range(count):
            start = min(video_duration - span, i * gap)
            end = min(video_duration, start + span)
            if end - start <= 0:
                break
            clips.append({
                'start': start,
                'end': end,
                'title': f'Fallback clip {i + 1}',
                'virality_score': 50,
                'hook_type': 'general',
                'duration': end - start,
            })
        return clips
