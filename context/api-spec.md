# API Specification

Base URL: `http://127.0.0.1:5000`

All request bodies are `multipart/form-data` unless noted. All JSON responses include a `"status": "ok"` or `"error": "..."` field.

---

## UI

### `GET /`
Renders the main single-page web UI (`templates/index.html`).

**Template variables injected:**
| Variable | Source |
|----------|--------|
| `defaults.title` | `VIDEO_TITLE_PREFIX + today's date` |
| `defaults.description` | `VIDEO_DESCRIPTION` |
| `defaults.privacy` | `VIDEO_PRIVACY` |
| `defaults.video_format` | `DEFAULT_VIDEO_FORMAT` |
| `defaults.script_provider` | `SCRIPT_PROVIDER` |
| `defaults.tts_provider` | `TTS_PROVIDER` |
| `defaults.elevenlabs_voice_id` | `ELEVENLABS_VOICE_ID` |

---

## Build & Upload

### `POST /build`
Build a narrated slideshow video from uploaded images.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `images` | file[] | ✅ | One or more image files (jpg/png). Sorted by natural filename order |
| `title` | string | | Video title. Defaults to `VIDEO_TITLE_PREFIX + date` |
| `description` | string | | Video description. Used as TTS narration text if TTS is enabled |
| `video_format` | `video` \| `short` | | Output resolution: `video`=16:9, `short`=9:16. Default: `VIDEO_FORMAT` |
| `video_style` | `static` \| `animated` | | `static`=plain slideshow, `animated`=Ken Burns pan/zoom with crossfade. Default: `static` |
| `thumbnail` | file | | Optional custom thumbnail image |
| `tts_provider` | `pyttsx3` \| `elevenlabs` \| `edge_tts` \| `xtts` | | TTS engine to use |
| `voice_id` | string | | ElevenLabs voice ID |
| `el_stability` | float | | ElevenLabs stability (0.0–1.0) |
| `el_similarity` | float | | ElevenLabs similarity boost (0.0–1.0) |
| `edge_voice` | string | | Edge-TTS voice short name e.g. `en-US-GuyNeural` |
| `edge_rate` | string | | Edge-TTS rate e.g. `+10%` |
| `edge_pitch` | string | | Edge-TTS pitch e.g. `+0Hz` |
| `speaker_wav` | string | | XTTS: filename from `voice_samples/` directory |

**Response (200):**
```json
{ "status": "ok", "title": "Daily Update 2026-05-01", "video_format": "video" }
```

**Response (400/500):**
```json
{ "error": "No images uploaded" }
```

**Notes:**
- Output written to `output.mp4` in project root
- YouTube upload is currently disabled (commented out in code)
- TTS is only applied if `ENABLE_TTS=true` in `.env`

---

## Voice Preview

### `POST /preview-voice`
Generate a short audio preview of the selected TTS voice using the description text.

**Request (multipart/form-data):** Same TTS params as `/build` (`description`, `tts_provider`, `voice_id`, `el_stability`, `el_similarity`, `edge_voice`, `edge_rate`, `edge_pitch`, `speaker_wav`)

**Response (200):** Audio bytes (`audio/mpeg` or `audio/wav`)

**Response (400):**
```json
{ "error": "TTS preview is disabled. Set ENABLE_TTS=true in .env." }
```

---

## Script Generation

### `POST /generate-script`
Generate a YouTube narration script from a topic using an LLM.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `topic` | string | ✅ | Topic or idea for the video |
| `provider` | `gemini` \| `huggingface` \| `ollama` | | LLM provider. Default: `SCRIPT_PROVIDER` |
| `duration` | `30s` \| `60s` \| `90s` \| `2min` \| `3min` | | Target duration. Default: `60s` |
| `video_format` | `video` \| `short` | | Tells LLM the format for context. Default: `DEFAULT_VIDEO_FORMAT` |

**Response (200):**
```json
{ "status": "ok", "script": "Your full narration script here...", "provider": "gemini" }
```

**Response (400/500):**
```json
{ "error": "GEMINI_API_KEY not set in .env" }
```

**Notes:**
- Word count is calculated as `seconds × 2.5 words/second`
- Max tokens = `word_count × 2.0` to allow for padding
- LLM is instructed to output narration text only (no markdown, no stage directions)

---

## Text to Video

### `POST /text-to-video`
Full AI pipeline: text → scene split → per-scene AI images + video clips → TTS → stitched MP4.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | ✅ | Full narration text to convert to video |
| `video_format` | `video` \| `short` | | Output resolution |
| `video_style` | `static` \| `animated` | | Visual style |
| `tts_provider` | string | | TTS engine |
| *(all TTS params)* | | | Same as `/build` |

**Response (200):** Video bytes (`video/mp4`) as `text_to_video.mp4` attachment

**Response (400/500):**
```json
{ "error": "HF_API_TOKEN not set in .env" }
```

**Notes:**
- Requires `HF_API_TOKEN` in `.env`
- Pipeline is slow: each scene requires 2 API calls (image + video)
- Falls back to static image if AI video clip generation fails

---

## Image Generation

### `POST /generate-image`
Generate a single AI image using Stable Diffusion XL.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | ✅ | Text description of the image |
| `video_format` | `video` \| `short` | | Aspect ratio: `video`=1344×768, `short`=768×1344 |

**Response (200):** PNG image bytes (`image/png`) as `generated_image.png` attachment

**Response (400/500):**
```json
{ "error": "HF_API_TOKEN not set in .env" }
```

---

## ElevenLabs

### `GET /elevenlabs-voices`
Fetch available voices from ElevenLabs API.

**Response (200):**
```json
{
  "status": "ok",
  "voices": [
    { "voice_id": "...", "name": "George", "category": "premade", "labels": {} }
  ]
}
```

**Response (400):**
```json
{ "error": "ELEVENLABS_API_KEY not set in .env" }
```

**Notes:** Returns a hardcoded fallback list if the API is unreachable (corporate proxy).

---

## Self-Hosted TTS

### `GET /local-tts/status`
Check which self-hosted TTS backends are installed.

**Response (200):**
```json
{ "available": true, "backends": { "xtts": true, "edge": true } }
```

---

### `GET /local-tts/voices`
List uploaded voice sample files (for XTTS voice cloning).

**Response (200):**
```json
{
  "status": "ok",
  "voices": [
    { "name": "my_voice", "filename": "my_voice.wav", "size_kb": 142.5 }
  ]
}
```

---

### `GET /local-tts/edge-voices`
Fetch all available Edge-TTS voices (cached after first call).

**Response (200):**
```json
{
  "status": "ok",
  "voices": [
    { "voice_id": "en-US-GuyNeural", "name": "Microsoft Guy Online (Natural)", "gender": "Male", "locale": "en-US" }
  ]
}
```

---

### `POST /local-tts/upload-voice`
Upload a voice sample for XTTS voice cloning.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `voice_file` | file | ✅ | Audio file: `.wav`, `.mp3`, `.ogg`, `.flac`, `.m4a`, `.webm` |
| `voice_name` | string | | Display name. Defaults to original filename stem |

**Response (200):**
```json
{ "status": "ok", "filename": "my_voice.wav", "size_kb": 142.5 }
```

**Notes:**
- Non-WAV files are automatically converted to WAV (22050 Hz mono) using bundled ffmpeg
- Original non-WAV file is deleted after successful conversion
- Saved to `voice_samples/` directory

---

### `POST /local-tts/delete-voice`
Delete a voice sample file.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `filename` | string | ✅ | Filename in `voice_samples/` to delete |

**Response (200):**
```json
{ "status": "ok" }
```

**Response (404):**
```json
{ "error": "Voice sample not found." }
```

---

### `POST /local-tts/synthesize`
Synthesize text to speech using a self-hosted backend.

**Request (multipart/form-data):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | ✅ | Text to synthesize |
| `backend` | `edge` \| `xtts` \| `auto` | | TTS backend. `auto` picks xtts if speaker given, else edge |
| `language` | string | | Language code (for XTTS). Default: `LOCAL_TTS_LANGUAGE` |
| `voice_filename` | string | XTTS | Filename from `voice_samples/` for voice cloning |
| `edge_voice` | string | Edge | Edge-TTS voice short name. Default: `en-US-GuyNeural` |
| `edge_rate` | string | Edge | Speaking rate e.g. `+0%`, `+10%`. Default: `+0%` |
| `edge_pitch` | string | Edge | Pitch adjustment e.g. `+0Hz`. Default: `+0Hz` |

**Response (200):** Audio bytes — `audio/mpeg` (edge) or `audio/wav` (xtts)

**Response (400/500):**
```json
{ "error": "No TTS backend installed. Run: pip install edge-tts" }
```
