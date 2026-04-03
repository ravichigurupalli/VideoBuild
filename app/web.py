from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import List
from werkzeug.utils import secure_filename

from flask import Flask, render_template, request, send_file

from .config import load_settings
from .tts import synthesize_to_file
from .video_builder import build_slideshow, default_title, natural_sort_key
from .youtube_client import upload_video

app = Flask(
    __name__, template_folder=str(Path(__file__).resolve().parent.parent / "templates")
)
settings = load_settings()


@app.get("/")
def index():
    return render_template(
        "index.html",
        defaults={
            "title": default_title(settings),
            "description": settings.video_description,
            "privacy": settings.video_privacy,
            "video_format": settings.default_video_format,
        },
    )


@app.post("/build")
def build():
    files = request.files.getlist("images")
    if not files:
        return {"error": "No images uploaded"}, 400

    title = request.form.get("title") or default_title(settings)
    description = request.form.get("description") or settings.video_description
    video_format = (request.form.get("video_format") or settings.default_video_format).strip().lower()
    if video_format not in {"video", "short"}:
        return {"error": "Invalid format. Use video or short."}, 400
    thumbnail_file = request.files.get("thumbnail")

    with tempfile.TemporaryDirectory(prefix="videobuild_") as tmpdir:
        tmp_path = Path(tmpdir)
        saved_paths: List[Path] = []
        thumbnail_path: Path | None = None
        for file_storage in sorted(files, key=lambda item: natural_sort_key(item.filename or "")):
            if not file_storage.filename:
                continue
            safe_name = secure_filename(Path(file_storage.filename).name)
            if not safe_name:
                continue
            dest = tmp_path / safe_name
            file_storage.save(dest)
            saved_paths.append(dest)

        if not saved_paths:
            return {"error": "No valid images"}, 400

        if thumbnail_file and thumbnail_file.filename:
            thumbnail_name = secure_filename(Path(thumbnail_file.filename).name)
            if thumbnail_name:
                thumbnail_path = tmp_path / thumbnail_name
                thumbnail_file.save(thumbnail_path)

        narration = description if settings.enable_tts else None
        video_path = build_slideshow(settings, saved_paths, narration=narration, video_format=video_format)
        #upload_video(settings, video_path, title, description, thumbnail_path=thumbnail_path)

    return {"status": "ok", "title": title, "video_format": video_format}


@app.post("/preview-voice")
def preview_voice():
    if not settings.enable_tts:
        return {"error": "TTS preview is disabled. Set ENABLE_TTS=true in .env."}, 400

    description = request.form.get("description") or settings.video_description
    if not description or not description.strip():
        return {"error": "Description is required for voice preview."}, 400

    voice_path = synthesize_to_file(settings, description)
    audio_bytes = voice_path.read_bytes()

    try:
        if voice_path.exists():
            voice_path.unlink()
        if voice_path.parent.exists():
            voice_path.parent.rmdir()
    except Exception:
        pass

    return send_file(
        io.BytesIO(audio_bytes),
        mimetype="audio/wav",
        as_attachment=False,
        download_name="voice-preview.wav",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
