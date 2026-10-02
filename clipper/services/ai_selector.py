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
        if not OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY is not set in environment variables.")
            
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
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
            
            # Remove response_format={"type": "json_object"} as many free/other models don't support it
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            response_content = completion.choices[0].message.content
            
            # Clean up potential markdown code blocks
            if "```json" in response_content:
                response_content = response_content.split("```json")[1].split("```")[0].strip()
            elif "```" in response_content:
                response_content = response_content.split("```")[1].split("```")[0].strip()
                
            try:
                data = json.loads(response_content)
            except json.JSONDecodeError:
                # Last resort: try to find start and end of JSON list/object
                start_bracket = response_content.find('{')
                end_bracket = response_content.rfind('}')
                if start_bracket != -1 and end_bracket != -1:
                    clean_json = response_content[start_bracket:end_bracket+1]
                    data = json.loads(clean_json)
                else:
                    raise
            
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
                raise ValueError("AI did not return any valid clips.")

            validated_clips.sort(key=lambda x: x['virality_score'], reverse=True)
            print(f"✅ AI selected {len(validated_clips)} complete viral clips:")
            for i, clip in enumerate(validated_clips[:n], 1):
                print(f"  {i}. {clip['title']} (Score: {clip['virality_score']}, Type: {clip['hook_type']})")
            
            return validated_clips[:n]
            
        except Exception as e:
            print(f"❌ AI clip selection failed: {e}. Using fallback method.")
            return self._fallback_selection(segments, video_duration, n, min_dur, max_dur)

    def _fallback_selection(self, segments, video_duration, n, min_dur, max_dur):
        clips = []
        used_segments = set()
        
        # Try to find n clips
        attempts = 0
        max_attempts = len(segments) * 2  # Limit attempts to avoid infinite loops

        while len(clips) < n and attempts < max_attempts:
            attempts += 1
            
            # Find a starting segment that hasn't been used
            available_indices = [i for i in range(len(segments)) if i not in used_segments]
            if not available_indices:
                break
                
            start_idx = random.choice(available_indices)
            
            current_duration = 0
            end_idx = start_idx
            
            # Extend the clip until we meet min_dur or hit max_dur
            while end_idx < len(segments):
                seg = segments[end_idx]
                if end_idx in used_segments and end_idx != start_idx:
                     # Stop if we hit a used segment (unless it's the start)
                    break
                
                segment_duration = seg['end'] - seg['start']
                if current_duration + segment_duration > max_dur:
                    break
                
                current_duration += segment_duration
                end_idx += 1
                
                if current_duration >= min_dur:
                    break
            
            # If we found a valid sequence
            if current_duration >= min_dur:
                # Mark segments as used
                for i in range(start_idx, end_idx):
                    used_segments.add(i)
                
                start_time = segments[start_idx]['start']
                end_time = segments[end_idx-1]['end']
                
                clips.append({
                    'start': start_time,
                    'end': end_time,
                    'title': f'Fallback clip {len(clips)+1}',
                    'virality_score': 50,
                    'hook_type': 'general',
                    'duration': end_time - start_time
                })
        
        return clips
