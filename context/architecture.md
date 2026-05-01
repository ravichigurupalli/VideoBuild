# Architecture

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `app/config.py` | Loads `.env` into a typed `Settings` dataclass; single source of truth for all config |
| `app/web.py` | Flask application; all HTTP endpoints; request parsing; delegates to domain modules |
| `app/video_builder.py` | Slideshow pipeline: image sorting → TTS → Ken Burns/static clips → BGM mix → MP4 write |
| `app/text_to_video.py` | AI video pipeline: text → scene split → per-scene AI image + AI video clip → TTS → stitch |
| `app/tts.py` | TTS dispatcher: routes to pyttsx3 / ElevenLabs / Edge-TTS / XTTS based on provider setting |
| `app/local_tts.py` | Self-hosted TTS: Edge-TTS backend, XTTS v2 backend, voice sample management, backend detection |
| `app/script_gen.py` | AI script generation: builds prompts and dispatches to Gemini / HuggingFace / Ollama |
| `app/image_gen.py` | HuggingFace SDXL image generation via REST API |
| `app/youtube_client.py` | OAuth2 credential management + YouTube Data API v3 resumable upload |
| `app/main.py` | CLI entry point: loads settings, builds slideshow, uploads to YouTube |

---

## Dependency Graph

```
web.py (Flask)
├── config.py          (Settings)
├── video_builder.py
│   ├── config.py
│   └── tts.py
│       ├── config.py
│       └── local_tts.py
│           └── [edge-tts, coqui-tts/TTS]
├── text_to_video.py
│   ├── config.py
│   ├── image_gen.py
│   ├── tts.py
│   └── video_builder.py  (shared helpers: _fit_image_clip, _resolve_output_resolution)
├── script_gen.py
│   └── [requests, huggingface_hub]
├── image_gen.py
│   └── [requests, Pillow]
├── local_tts.py
│   └── [edge-tts, TTS/coqui, soundfile, torchaudio, torch]
├── tts.py
│   └── [pyttsx3, requests, local_tts]
└── youtube_client.py
    └── [google-api-python-client, google-auth-oauthlib]

main.py
├── config.py
├── video_builder.py
└── youtube_client.py
```

---

## Data Flow Diagrams

### Feature 1: Build & Upload Video

```
Browser (multipart/form-data)
  images[] + title + description + tts_provider + voice params
       │
       ▼
web.py /build
  → save images to TemporaryDirectory
  → video_builder.build_slideshow()
       │
       ├─ tts.synthesize_to_file()          ← generates narration .wav/.mp3
       │     └─ pyttsx3 / ElevenLabs / edge_tts / xtts
       │
       ├─ ImageClip(each image) OR _ken_burns_clip()
       ├─ concatenate_videoclips()
       ├─ AudioFileClip(bgm) + AudioFileClip(voice)
       ├─ CompositeAudioClip([bgm, voice])
       └─ video.write_videofile() → output.mp4
  → [upload_video() — currently commented out in web.py]
  → return {"status": "ok"}
```

### Feature 2: Generate Image

```
Browser (form: prompt + video_format)
       │
       ▼
web.py /generate-image
  → image_gen.generate_image()
       └─ POST https://router.huggingface.co/hf-inference/models/
              stabilityai/stable-diffusion-xl-base-1.0
          params: width, height, num_inference_steps=30, guidance_scale=7.5
  → return image/png bytes
```

### Feature 3: Text to Video (AI Generated)

```
Browser (form: text + video_format + video_style + tts_provider + ...)
       │
       ▼
web.py /text-to-video
  → text_to_video.text_to_video()
       │
       ├─ _split_into_scenes(text)          ← splits on sentence boundaries (2 sentences/scene)
       │
       ├─ [for each scene]:
       │     ├─ image_gen.generate_image()  ← SDXL per-scene image
       │     └─ _generate_video_clip()      ← ali-vilab/text-to-video-ms-1.7b per-scene clip
       │
       ├─ [stitch clips]:
       │     ├─ VideoFileClip OR ImageClip OR Ken Burns
       │     └─ _apply_crossfade() if animated
       │
       ├─ tts.synthesize_to_file(full_narration)
       └─ video.write_videofile() → output.mp4
  → return video/mp4 bytes
```

### Feature 4: Self-Hosted TTS

```
Browser (form: text + backend + edge_voice/voice_filename + ...)
       │
       ▼
web.py /local-tts/synthesize
  → local_tts.synthesize_local()
       ├─ backend="edge"  → synthesize_edge()
       │     └─ edge_tts.Communicate(text, voice).save()
       └─ backend="xtts"  → synthesize_xtts()
             ├─ _get_tts() — lazy load XTTS v2 model (cached in _tts_instance)
             └─ tts.tts_to_file(text, speaker_wav=..., language=...)
  → return audio/mpeg or audio/wav bytes
```

### Feature 5: Script Generation

```
Browser (form: topic + provider + duration + video_format)
       │
       ▼
web.py /generate-script
  → script_gen.generate_script()
       ├─ _build_messages(topic, duration, format)
       │     └─ word_count = seconds * 2.5
       ├─ provider="gemini"       → POST generativelanguage.googleapis.com
       ├─ provider="huggingface"  → InferenceClient(Qwen/Qwen2.5-7B-Instruct)
       └─ provider="ollama"       → POST localhost:11434/api/chat (llama3.2)
  → return {"script": "..."}
```

---

## Key Design Decisions

### Settings Dataclass (config.py)
All configuration is loaded once at server startup into a frozen `Settings` dataclass. Modules receive it as a parameter — no global state, no repeated env reads.

### Lazy XTTS Model Loading (local_tts.py)
The 1.8 GB XTTS v2 model is loaded on first synthesis request and cached in `_tts_instance`. A `threading.Lock` prevents duplicate loads under concurrent requests.

### Temporary Directory Cleanup
Every pipeline that generates files (TTS audio, video clips) uses `tempfile.TemporaryDirectory` or manual `unlink()`/`rmdir()` after reading the bytes into memory. No leftover temp files accumulate.

### TTS Fallback Chain (tts.py)
If ElevenLabs or Edge-TTS fails, `synthesize_to_file()` catches the exception and falls back to `pyttsx3` (offline, always available). XTTS does not fall back automatically — it raises if `speaker_wav` is missing.

### Image Fit Strategy (video_builder.py)
Images are aspect-ratio-fitted to the target resolution: scale to fill the shorter dimension, then center-crop. This prevents letterboxing/pillarboxing.

### YouTube Upload Disabled in Web UI
`upload_video()` is commented out in `web.py /build`. It is only active in `app/main.py` (CLI). This is intentional — the web UI is used for preview/local build, CLI for scheduled uploads.

### Audio Mixing Levels
- BGM with voice: 15% volume (`BGM_VOLUME_WITH_VOICE=0.15`)
- BGM without voice: 35% volume (`BGM_VOLUME=0.35`)
- Voice narration: 60% volume (`AUDIO_VOLUME=0.6`)

### Video Format Support
- `video` → 1920×1080 (16:9 landscape)
- `short` → 1080×1920 (9:16 portrait for YouTube Shorts)

---

## Frontend Architecture (index.html)

Single HTML file (~800 lines) with:
- **Sidebar navigation** (dark, fixed position, collapsible via hamburger/◀ buttons)
- **4 content panels** (cards), only one visible at a time, toggled by sidebar clicks
- **Vanilla JS** — no framework; all API calls via `fetch()` with `FormData`
- **Inline CSS** — no external stylesheet
- Dynamic UI elements: voice dropdown refresh, ElevenLabs slider controls, mode tabs (Generate Script / Custom Text), recorder for voice samples
