from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from flask import Flask, render_template, request

from .config import load_settings
from .video_builder import build_slideshow, default_title
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
        },
    )


@app.post("/build")
def build():
    files = request.files.getlist("images")
    if not files:
        return {"error": "No images uploaded"}, 400

    title = request.form.get("title") or default_title(settings)
    description = request.form.get("description") or settings.video_description

    with tempfile.TemporaryDirectory(prefix="videobuild_") as tmpdir:
        tmp_path = Path(tmpdir)
        saved_paths: List[Path] = []
        for idx, file_storage in enumerate(files, start=1):
            if not file_storage.filename:
                continue
            ext = Path(file_storage.filename).suffix or ".png"
            dest = tmp_path / f"slide_{idx:03d}{ext}"
            file_storage.save(dest)
            saved_paths.append(dest)

        if not saved_paths:
            return {"error": "No valid images"}, 400

        narration = description if settings.enable_tts else None
        video_path = build_slideshow(settings, saved_paths, narration=narration)
        #upload_video(settings, video_path, title, description)

    return {"status": "ok", "title": title}


if __name__ == "__main__":
    app.run(debug=True, port=5000)
