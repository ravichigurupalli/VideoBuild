# Configuration Reference

All settings are loaded from `.env` in the project root via `python-dotenv`.
Copy `.env.example` to `.env` and override as needed.

The `Settings` dataclass is defined in `app/config.py` and populated at server startup.

---

## Video Output

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `OUTPUT_FILENAME` | string | `output.mp4` | Filename for rendered video, relative to project root |
| `DEFAULT_VIDEO_FORMAT` | `video` \| `short` | `video` | Default format: `video`=1920×1080 (16:9), `short`=1080×1920 (9:16) |
| `KEEP_OUTPUT` | bool | `false` | If `true`, don't delete `output.mp4` after upload (CLI mode) |

---

## Video Resolution

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `RESOLUTION_WIDTH` | int | `1920` | Width for standard video format |
| `RESOLUTION_HEIGHT` | int | `1080` | Height for standard video format |
| `SHORT_RESOLUTION_WIDTH` | int | `1080` | Width for Shorts format |
| `SHORT_RESOLUTION_HEIGHT` | int | `1920` | Height for Shorts format |
| `FPS` | int | `30` | Frames per second |
| `BITRATE` | string | `4000k` | Video bitrate for libx264 encoder |

---

## Slideshow Timing

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `SECONDS_PER_IMAGE` | int | `4` | Base display duration per image. Actual duration may be longer if TTS audio is longer |

---

## YouTube Metadata

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `VIDEO_TITLE_PREFIX` | string | `Daily Update` | Prepended to today's date for auto-generated titles (e.g. `Daily Update 2026-05-01`) |
| `VIDEO_DESCRIPTION` | string | `Automated daily upload` | Default description and TTS narration text |
| `VIDEO_PRIVACY` | `private` \| `public` \| `unlisted` | `private` | YouTube upload privacy status |
| `CATEGORY_ID` | string | `22` | YouTube category ID (`22` = People & Blogs) |
| `CLIENT_SECRET_FILE` | string | `client_secret.json` | Path to Google OAuth2 credentials file |
| `TOKEN_FILE` | string | `token.json` | Path to cached OAuth2 token file |

---

## TTS — General

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ENABLE_TTS` | bool | `false` | Enable text-to-speech narration in video builds |
| `TTS_PROVIDER` | `pyttsx3` \| `elevenlabs` \| `edge_tts` \| `xtts` | `pyttsx3` | Default TTS engine |
| `TTS_VOICE` | string | *(empty)* | pyttsx3 voice ID (system-specific). Leave empty for system default |
| `TTS_RATE` | int | `180` | pyttsx3 speaking rate (words per minute) |

---

## TTS — ElevenLabs

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ELEVENLABS_API_KEY` | string | *(empty)* | ElevenLabs API key (required for ElevenLabs TTS) |
| `ELEVENLABS_VOICE_ID` | string | `57WpXhyNwaU0uXgMrmDS` | Default ElevenLabs voice ID |
| `ELEVENLABS_MODEL_ID` | string | `eleven_multilingual_v2` | ElevenLabs model |
| `ELEVENLABS_STABILITY` | float | `0.45` | Voice stability (0.0=more variable, 1.0=more stable) |
| `ELEVENLABS_SIMILARITY_BOOST` | float | `0.80` | Similarity boost (higher = closer to original voice) |

---

## TTS — Self-Hosted (XTTS v2)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `LOCAL_TTS_MODEL` | string | `tts_models/multilingual/multi-dataset/xtts_v2` | Coqui TTS model name. Downloaded on first use (~1.8 GB) |
| `LOCAL_TTS_DEVICE` | `auto` \| `cpu` \| `cuda` | `auto` | Inference device. `auto` uses CUDA if available |
| `LOCAL_TTS_LANGUAGE` | string | `en` | Default language for XTTS synthesis |
| `VOICE_SAMPLES_DIR` | string | `voice_samples` | Directory for uploaded voice sample files (relative to project root) |

---

## Audio Mixing

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `AUDIO_VOLUME` | float | `0.6` | TTS narration volume (0.0–1.0) |
| `BGM_VOLUME` | float | `0.35` | Background music volume when no TTS narration |
| `BGM_VOLUME_WITH_VOICE` | float | `0.15` | Background music volume when TTS narration is present (ducked) |

---

## AI Services

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `HF_API_TOKEN` | string | *(empty)* | HuggingFace API token. Required for image generation, text-to-video, and HuggingFace script generation |
| `GEMINI_API_KEY` | string | *(empty)* | Google Gemini API key. Required for Gemini script generation |
| `SCRIPT_PROVIDER` | `gemini` \| `huggingface` \| `ollama` | `gemini` | Default LLM provider for script generation |

---

## Notes

### Proxy / SSL
If behind a corporate proxy, set:
```
HTTPS_PROXY=http://your-proxy:8080
```
ElevenLabs requests use `requests.Session` with `verify=False` and proxy passthrough.
XTTS model download also bypasses SSL verification (monkey-patched in `local_tts._get_tts()`).

### pyttsx3 Voice IDs (Windows)
To list available voices:
```python
import pyttsx3
engine = pyttsx3.init()
for v in engine.getProperty('voices'):
    print(v.id, v.name)
```
Common Windows voices: `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0`

### XTTS Model Cache
Downloaded to: `%LOCALAPPDATA%\tts\` on Windows
(e.g. `C:\Users\<username>\AppData\Local\tts\tts_models--multilingual--multi-dataset--xtts_v2\`)
Downloaded only once; reused on all subsequent server starts.
