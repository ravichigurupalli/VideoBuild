# VideoBuild (Local-first YouTube uploader)

Minimal template to render a slideshow video locally and upload it to YouTube once per day. Designed for Windows Task Scheduler (or any cron) with no cloud hosting.

## Prereqs
- Python 3.10+
- FFmpeg on PATH
- Google Cloud project with YouTube Data API v3 enabled
- OAuth 2.0 Desktop credentials JSON (`client_secret.json`)

## Setup
1) Clone/copy this folder.
2) Install deps (venv recommended):
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```
3) Place `client_secret.json` in the project root.
4) Copy `.env.example` to `.env` and adjust paths if needed.
5) Add input assets:
   - `assets/slides/` → your .jpg/.png images
   - `assets/bgm.mp3` → optional background music

## Run once (interactive auth first time)
```bash
python -m app.main
```
This will open a browser for Google auth and create `token.json` locally.

## Option 2: Web UI (upload images via browser)
```bash
python -m flask --app app.web run --port 5000
```
Then open http://localhost:5000, choose multiple images, edit title/description, and click **Start**. The server stores uploads in a temp folder, renders, uploads to YouTube, then discards temp files. Uses the same env/config as the CLI.

## Schedule daily (Windows)
Use Task Scheduler → Create Basic Task → Action: `python` with arguments `-m app.main` and "Start in" set to the project folder. Ensure the machine is on/logged in at the scheduled time.

## Customizing
- Edit `app/video_builder.py` to change durations, resolution, overlays, etc.
- Edit `app/youtube_client.py` for metadata defaults.
- Environment overrides live in `.env` (see defaults in `app/config.py`).

## Notes
- Output file is deleted after a successful upload to save space. Remove that line if you want to keep renders.
- Keep your `client_secret.json` and `token.json` private.
