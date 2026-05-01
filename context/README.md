# VideoBuild — Project Overview

**VideoBuild** is a local-first Python web application that builds narrated slideshow videos, generates AI images, creates fully AI-generated text-to-video content, and synthesizes speech — all from a browser UI backed by a Flask server.

---

## Purpose

| Goal | Description |
|------|-------------|
| Local rendering | No cloud rendering fees; ffmpeg/MoviePy run on your machine |
| Multiple TTS engines | pyttsx3 (offline), ElevenLabs (cloud), Edge-TTS (free), XTTS v2 (local voice clone) |
| AI image generation | Stable Diffusion XL via HuggingFace Inference API |
| AI video generation | Text-to-video via HuggingFace (ali-vilab/text-to-video-ms-1.7b) |
| Script generation | LLM-backed script writing (Gemini, HuggingFace, Ollama) |
| YouTube upload | OAuth2 upload via YouTube Data API v3 (currently disabled in UI, available in CLI) |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12, Flask 3.0 |
| Video rendering | MoviePy 1.0, Pillow, NumPy |
| TTS | pyttsx3, ElevenLabs REST API, edge-tts, Coqui XTTS v2 |
| AI models | HuggingFace Inference API (SDXL, text-to-video), Google Gemini API, Ollama |
| YouTube | google-api-python-client, google-auth-oauthlib |
| Audio conversion | imageio-ffmpeg (bundled ffmpeg exe) |
| Config | python-dotenv (.env file) |
| Frontend | Vanilla HTML/CSS/JS, no framework |

---

## Project Structure

```
VideoBuild/
├── app/
│   ├── config.py          # Settings dataclass + .env loader
│   ├── web.py             # Flask app + all API endpoints
│   ├── video_builder.py   # Slideshow video pipeline
│   ├── text_to_video.py   # AI text-to-video pipeline
│   ├── tts.py             # TTS dispatcher (pyttsx3, ElevenLabs, Edge, XTTS)
│   ├── local_tts.py       # Self-hosted TTS: Edge-TTS + XTTS v2
│   ├── script_gen.py      # AI script generation (Gemini/HF/Ollama)
│   ├── image_gen.py       # HuggingFace SDXL image generation
│   ├── youtube_client.py  # YouTube Data API v3 upload
│   └── main.py            # CLI entry point
├── templates/
│   └── index.html         # Full single-page web UI (sidebar nav)
├── voice_samples/         # Uploaded voice sample .wav files (for XTTS)
├── assets/
│   ├── slides/            # Input images for slideshow
│   └── bgm.mp3            # Background music
├── context/               # ← This documentation folder
├── .env                   # Local config (gitignored)
├── .env.example           # Config template
├── requirements.txt       # Python dependencies
├── client_secret.json     # Google OAuth credentials (gitignored)
└── output.mp4             # Rendered video output
```

---

## Prerequisites

- **Python 3.10+** (tested on 3.12.3)
- **FFmpeg** — on PATH for MoviePy; bundled `imageio-ffmpeg` used for audio conversion
- **Google Cloud project** with YouTube Data API v3 enabled (for upload)
- **OAuth 2.0 Desktop credentials** (`client_secret.json`) for YouTube upload

---

## Setup

```bash
# 1. Create virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env
# Edit .env with your API keys

# 4. Place client_secret.json in project root (for YouTube upload)

# 5. Add input images to assets/slides/ (for slideshow mode)
```

---

## Running

### Web UI (recommended)
```bash
python -m flask --app app.web run --port 5000
```
Open `http://localhost:5000` in your browser.

### CLI (slideshow + upload only)
```bash
python -m app.main
```
Reads images from `assets/slides/`, builds video, uploads to YouTube.

---

## Web UI Navigation

The UI has a **left sidebar** with 4 sections:

| Section | Description |
|---------|-------------|
| 🎬 Build & Upload | Upload images → build narrated slideshow → upload to YouTube |
| 🖼️ Generate Image | Generate an AI image from a text prompt (SDXL) |
| 🎥 Text to Video | Full AI pipeline: text → scenes → AI images → AI video → TTS narration → MP4 |
| 🎙️ Self-Hosted TTS | Standalone TTS: Edge-TTS (Microsoft) or XTTS v2 (local voice cloning) |

---

## Required API Keys (in .env)

| Key | Required For |
|-----|-------------|
| `GEMINI_API_KEY` | Script generation via Google Gemini |
| `HF_API_TOKEN` | Image generation, text-to-video, HuggingFace script generation |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS |
| `client_secret.json` | YouTube upload (OAuth2) |
