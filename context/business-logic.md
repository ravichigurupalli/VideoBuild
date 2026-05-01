# Business Logic

---

## 1. Build & Upload Video Pipeline

### Entry Point
`web.py /build` → `video_builder.build_slideshow()`

### Step-by-Step

1. **Image ingestion**
   - Files saved to a `TemporaryDirectory`
   - Sorted using `natural_sort_key()`: numeric parts compared as integers (e.g. `img2 < img10`)

2. **TTS narration** (if `ENABLE_TTS=true` and description is not empty)
   - `tts.synthesize_to_file()` is called with the video description as the narration text
   - Returns a temp `.wav` or `.mp3` file path

3. **Timeline calculation**
   - `base_duration = num_images × SECONDS_PER_IMAGE`
   - `target_duration = max(base_duration, voice_audio.duration)`
   - `per_image_duration = target_duration / num_images`
   - The last image gets 2 seconds less to avoid a long tail

4. **Clip generation (per image)**
   - `static` style: `ImageClip` + center-crop fit to resolution
   - `animated` style: `_ken_burns_clip()` — randomly picks one of 4 effects: `zoom_in`, `zoom_out`, `pan_left`, `pan_right`

5. **Crossfade transitions** (animated only)
   - Each clip (except first/last) gets `crossfadein(0.5s)` and `crossfadeout(0.5s)`

6. **Audio mixing**
   - BGM loaded and looped to video duration
   - If voice exists: BGM at 15% volume + voice at 60% volume → `CompositeAudioClip`
   - If no voice: BGM at 35% volume
   - If no BGM file: voice only

7. **Render**
   - `video.write_videofile()` with codec=`libx264`, audio=`aac`, 4 threads, preset=`medium`
   - Output: `output.mp4` in project root

8. **Cleanup**
   - TTS temp dir deleted after render
   - TemporaryDirectory with uploaded images auto-deleted on context exit

---

## 2. TTS Provider Dispatch Logic

### Entry Point
`tts.synthesize_to_file()` — called by both `video_builder` and `text_to_video`

### Decision Tree

```
provider param or settings.tts_provider
         │
         ├─ "elevenlabs"
         │       └─ _synthesize_elevenlabs()
         │             ├─ SUCCESS → return voice.mp3
         │             └─ FAIL    → fallback: _synthesize_pyttsx3()
         │
         ├─ "edge_tts"
         │       └─ local_tts.synthesize_edge()
         │             ├─ SUCCESS → return voice.mp3
         │             └─ FAIL    → fallback: _synthesize_pyttsx3()
         │
         ├─ "xtts"
         │       └─ local_tts.synthesize_xtts()
         │             ├─ requires speaker_wav (raises if missing)
         │             ├─ SUCCESS → return voice.wav
         │             └─ FAIL    → fallback: _synthesize_pyttsx3()
         │
         └─ anything else (default "pyttsx3")
                 └─ _synthesize_pyttsx3()
                       └─ always returns voice.wav
```

### Fallback Behavior
- ElevenLabs and Edge-TTS: any exception triggers silent fallback to pyttsx3
- XTTS: exception triggers fallback to pyttsx3 (but missing `speaker_wav` raises before even trying)
- pyttsx3: never falls back — always succeeds (uses system voices)

---

## 3. Self-Hosted TTS Backend Selection

### Entry Point
`local_tts.synthesize_local()` — called from `web.py /local-tts/synthesize`

### Auto-selection Logic (`backend="auto"`)
```
speaker_wav provided AND xtts installed  →  use xtts
edge-tts installed                       →  use edge
xtts installed (no speaker_wav)          →  use xtts (will error at synthesis)
nothing installed                        →  raise RuntimeError
```

### XTTS Model Loading (Lazy)
- Model is ~1.8 GB, loaded on first synthesis request
- Cached in module-level `_tts_instance`
- `threading.Lock` (`_tts_lock`) prevents duplicate loads
- Device auto-selects: `cuda` if GPU available, else `cpu`

### Edge-TTS Voice Caching
- `fetch_edge_voices()` result cached in `_edge_voices_cache` (module-level)
- Only fetched once per server lifetime

---

## 4. Text-to-Video (AI Generated) Pipeline

### Entry Point
`web.py /text-to-video` → `text_to_video.text_to_video()`

### Scene Splitting Algorithm
```python
sentences = re.split(r'(?<=[.!?])\s+', text)   # split on sentence boundaries
scenes = [sentences[i:i+2] for i in range(0, len, 2)]  # group 2 sentences per scene
```
Each scene gets a `visual_prompt`:
> `"cinematic high quality illustration of: {narration}, vibrant colors, detailed, 4K, professional photography"`

### Per-Scene Processing
For each scene:
1. **Image**: `generate_image(visual_prompt)` via SDXL on HuggingFace
2. **Video clip**: `_generate_video_clip(visual_prompt)` via ali-vilab/text-to-video-ms-1.7b
3. **Clip selection priority**:
   - AI video clip exists → use it; if shorter than target, pad with static image
   - AI video clip failed → use image with Ken Burns (animated) or static
   - Both failed → use black `ColorClip`

### Target Duration per Scene
```
target_duration = max(len(narration.split()) × 0.4 seconds, 3.0)
```
Assumes ~150 words/minute speaking pace.

### Final Stitch
- Clips concatenated with `method="compose"` (animated) or `"chain"` (static)
- TTS of full narration + BGM mixed as in the slideshow pipeline
- Rendered to `output.mp4`

---

## 5. AI Script Generation

### Entry Point
`web.py /generate-script` → `script_gen.generate_script()`

### Word Budget Calculation
```
words_per_second = 2.5
word_count = duration_seconds × 2.5
max_tokens = word_count × 2.0
```

### Prompt Structure
```
System: "You are a professional YouTube script writer..."
User:   "Write a compelling narration script for a {format} about: '{topic}'"
        "Target length: ~{word_count} words ({seconds} seconds)"
        "Output ONLY the script text, nothing else"
```

### Provider Routing
| Provider | Model | API |
|----------|-------|-----|
| `gemini` | `gemini-2.0-flash` | `generativelanguage.googleapis.com/v1beta` |
| `huggingface` | `Qwen/Qwen2.5-7B-Instruct` | HuggingFace InferenceClient |
| `ollama` | `llama3.2` | `localhost:11434/api/chat` |

---

## 6. Voice Sample Upload & Conversion

### Entry Point
`web.py /local-tts/upload-voice`

### Flow
```
Upload file (any supported audio format)
        │
        ├─ ext == ".wav"  →  save directly to voice_samples/name.wav
        │
        └─ ext != ".wav"  →  save as temp file
                               → _convert_to_wav() using imageio-ffmpeg bundled exe
                               │    ffmpeg -y -i src -ar 22050 -ac 1 dst.wav
                               ├─ SUCCESS → delete original, keep .wav
                               └─ FAIL    → keep original (non-wav, XTTS may reject)
```

### Why 22050 Hz Mono?
XTTS v2 expects 22050 Hz sample rate for voice cloning. Mono reduces file size.

### Supported Input Formats
`.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`, `.webm`

The `.webm` format is common because browser `MediaRecorder` API records in WebM by default.

---

## 7. YouTube Upload (CLI Only)

### Entry Point
`app/main.py` → `youtube_client.upload_video()`

### OAuth2 Flow
1. Check `token.json` for cached credentials
2. If missing or expired: open browser → `InstalledAppFlow.run_local_server()`
3. Save new credentials to `token.json`
4. Build `youtube` service object

### Upload
- Resumable chunked upload via `MediaFileUpload(chunksize=-1, resumable=True)`
- Polls `request.next_chunk()` until `response` is not None
- Sets privacy from `VIDEO_PRIVACY` env var

### Thumbnail
- Optional: if `thumbnail_path` provided, calls `youtube.thumbnails().set()`

### Web UI Status
YouTube upload is **commented out** in `web.py /build`:
```python
#upload_video(settings, video_path, title, description, thumbnail_path=thumbnail_path)
```
To enable, uncomment this line.

---

## 8. Image Fitting Strategy

Used in both `video_builder.py` and `text_to_video.py` via shared `_fit_image_clip()`.

```
If image is wider than target (clip_ratio > target_ratio):
    scale to target_height (letterbox horizontal space)
Else:
    scale to target_width (pillarbox vertical space)

Then center-crop to exact target resolution
```
This ensures no black bars — the image always fills the frame.

---

## 9. Ken Burns Effect

Randomly selects one of 4 animation effects per clip:
- **zoom_in**: scale from 1.0× to 1.2× over clip duration
- **zoom_out**: scale from 1.2× to 1.0× over clip duration
- **pan_left**: fixed 1.15× scale, crop window moves right→left
- **pan_right**: fixed 1.15× scale, crop window moves left→right

Implementation works at 1.3× oversized canvas, then crops to target resolution at each frame. Uses `VideoClip(make_frame)` with per-frame NumPy/Pillow rendering.
