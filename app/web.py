from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import List
from werkzeug.utils import secure_filename

from flask import Flask, render_template, request, send_file

from .config import load_settings
from .image_gen import generate_image
from .script_gen import generate_script, DURATION_OPTIONS, PROVIDERS
from .text_to_video import text_to_video, VIDEO_STYLES
from .local_tts import (
    list_voice_samples, synthesize_local, is_available as local_tts_available,
    available_backends, fetch_edge_voices,
)
from .tts import synthesize_to_file, fetch_elevenlabs_voices, TTS_PROVIDERS
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
            "script_provider": settings.script_provider,
            "tts_provider": settings.tts_provider,
            "elevenlabs_voice_id": settings.elevenlabs_voice_id,
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
    video_style = (request.form.get("video_style") or "static").strip().lower()
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

        tts_provider = (request.form.get("tts_provider") or settings.tts_provider).strip().lower()
        voice_id = (request.form.get("voice_id") or "").strip() or None
        el_stability = float(request.form.get("el_stability")) if request.form.get("el_stability") else None
        el_similarity = float(request.form.get("el_similarity")) if request.form.get("el_similarity") else None
        edge_voice = (request.form.get("edge_voice") or "").strip() or None
        edge_rate = (request.form.get("edge_rate") or "").strip() or None
        edge_pitch = (request.form.get("edge_pitch") or "").strip() or None
        speaker_wav_name = (request.form.get("speaker_wav") or "").strip()
        speaker_wav = str(settings.voice_samples_dir / secure_filename(speaker_wav_name)) if speaker_wav_name else None
        narration = description if settings.enable_tts else None
        try:
            video_path = build_slideshow(
                settings, saved_paths, narration=narration, video_format=video_format,
                video_style=video_style, tts_provider=tts_provider, voice_id=voice_id,
                el_stability=el_stability, el_similarity=el_similarity,
                edge_voice=edge_voice, edge_rate=edge_rate, edge_pitch=edge_pitch,
                speaker_wav=speaker_wav,
            )
        except Exception as exc:
            print(f"Build failed: {exc}")
            return {"error": str(exc)}, 500
        #upload_video(settings, video_path, title, description, thumbnail_path=thumbnail_path)

    return {"status": "ok", "title": title, "video_format": video_format}


@app.post("/preview-voice")
def preview_voice():
    if not settings.enable_tts:
        return {"error": "TTS preview is disabled. Set ENABLE_TTS=true in .env."}, 400

    description = request.form.get("description") or settings.video_description
    if not description or not description.strip():
        return {"error": "Description is required for voice preview."}, 400

    tts_provider = (request.form.get("tts_provider") or settings.tts_provider).strip().lower()
    voice_id = (request.form.get("voice_id") or "").strip() or None
    el_stability = float(request.form.get("el_stability")) if request.form.get("el_stability") else None
    el_similarity = float(request.form.get("el_similarity")) if request.form.get("el_similarity") else None
    edge_voice = (request.form.get("edge_voice") or "").strip() or None
    edge_rate = (request.form.get("edge_rate") or "").strip() or None
    edge_pitch = (request.form.get("edge_pitch") or "").strip() or None
    speaker_wav_name = (request.form.get("speaker_wav") or "").strip()
    speaker_wav = str(settings.voice_samples_dir / secure_filename(speaker_wav_name)) if speaker_wav_name else None

    try:
        voice_path = synthesize_to_file(
            settings, description, tts_provider=tts_provider, voice_id=voice_id,
            el_stability=el_stability, el_similarity=el_similarity,
            edge_voice=edge_voice, edge_rate=edge_rate, edge_pitch=edge_pitch,
            speaker_wav=speaker_wav,
        )
    except Exception as exc:
        print(f"Preview voice failed: {exc}")
        return {"error": str(exc)}, 500

    audio_bytes = voice_path.read_bytes()
    mime = "audio/mpeg" if voice_path.suffix == ".mp3" else "audio/wav"
    ext = voice_path.suffix

    try:
        if voice_path.exists():
            voice_path.unlink()
        if voice_path.parent.exists():
            voice_path.parent.rmdir()
    except Exception:
        pass

    return send_file(
        io.BytesIO(audio_bytes),
        mimetype=mime,
        as_attachment=False,
        download_name=f"voice-preview{ext}",
    )


@app.get("/elevenlabs-voices")
def elevenlabs_voices():
    if not settings.elevenlabs_api_key:
        return {"error": "ELEVENLABS_API_KEY not set in .env"}, 400
    try:
        voices = fetch_elevenlabs_voices(settings.elevenlabs_api_key)
        return {"status": "ok", "voices": voices}
    except Exception as exc:
        return {"error": str(exc)}, 500


@app.post("/generate-script")
def gen_script():
    topic = (request.form.get("topic") or "").strip()
    if not topic:
        return {"error": "Topic is required."}, 400

    provider = (request.form.get("provider") or settings.script_provider).strip().lower()
    if provider not in PROVIDERS:
        return {"error": f"Invalid provider. Use one of: {', '.join(PROVIDERS)}"}, 400

    duration = (request.form.get("duration") or "60s").strip()
    if duration not in DURATION_OPTIONS:
        return {"error": f"Invalid duration. Use one of: {', '.join(DURATION_OPTIONS)}"}, 400

    video_format = (request.form.get("video_format") or settings.default_video_format).strip().lower()
    if video_format not in {"video", "short"}:
        return {"error": "Invalid format. Use video or short."}, 400

    try:
        script = generate_script(
            topic,
            provider=provider,
            hf_token=settings.hf_api_token,
            gemini_api_key=settings.gemini_api_key,
            duration_label=duration,
            video_format=video_format,
        )
        return {"status": "ok", "script": script, "provider": provider}
    except Exception as exc:
        print(f"Script generation failed: {exc}")
        return {"error": str(exc)}, 500


@app.post("/text-to-video")
def t2v():
    if not settings.hf_api_token:
        return {"error": "HF_API_TOKEN not set in .env"}, 400

    text = (request.form.get("text") or "").strip()
    if not text:
        return {"error": "Text content is required."}, 400

    video_format = (request.form.get("video_format") or settings.default_video_format).strip().lower()
    if video_format not in {"video", "short"}:
        return {"error": "Invalid format. Use video or short."}, 400

    video_style = (request.form.get("video_style") or "static").strip().lower()
    if video_style not in VIDEO_STYLES:
        return {"error": f"Invalid style. Use one of: {', '.join(VIDEO_STYLES)}"}, 400

    tts_provider = (request.form.get("tts_provider") or settings.tts_provider).strip().lower()
    voice_id = (request.form.get("voice_id") or "").strip() or None
    el_stability = float(request.form.get("el_stability")) if request.form.get("el_stability") else None
    el_similarity = float(request.form.get("el_similarity")) if request.form.get("el_similarity") else None
    edge_voice = (request.form.get("edge_voice") or "").strip() or None
    edge_rate = (request.form.get("edge_rate") or "").strip() or None
    edge_pitch = (request.form.get("edge_pitch") or "").strip() or None
    speaker_wav_name = (request.form.get("speaker_wav") or "").strip()
    speaker_wav = str(settings.voice_samples_dir / secure_filename(speaker_wav_name)) if speaker_wav_name else None

    try:
        output_path = text_to_video(
            settings, text,
            video_format=video_format,
            video_style=video_style,
            tts_provider=tts_provider,
            voice_id=voice_id,
            el_stability=el_stability,
            el_similarity=el_similarity,
            edge_voice=edge_voice,
            edge_rate=edge_rate,
            edge_pitch=edge_pitch,
            speaker_wav=speaker_wav,
        )
        return send_file(
            str(output_path),
            mimetype="video/mp4",
            as_attachment=True,
            download_name="text_to_video.mp4",
        )
    except Exception as exc:
        print(f"Text-to-video failed: {exc}")
        return {"error": str(exc)}, 500


@app.post("/generate-image")
def gen_image():
    if not settings.hf_api_token:
        return {"error": "HF_API_TOKEN not set in .env"}, 400

    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return {"error": "Prompt text is required."}, 400

    video_format = (request.form.get("video_format") or settings.default_video_format).strip().lower()
    if video_format not in {"video", "short"}:
        return {"error": "Invalid format. Use video or short."}, 400

    try:
        with tempfile.TemporaryDirectory(prefix="videobuild_img_") as tmpdir:
            out_path = Path(tmpdir) / "generated.png"
            generate_image(prompt, settings.hf_api_token, video_format=video_format, output_path=out_path)
            image_bytes = out_path.read_bytes()

        return send_file(
            io.BytesIO(image_bytes),
            mimetype="image/png",
            as_attachment=True,
            download_name="generated_image.png",
        )
    except Exception as exc:
        print(f"Image generation failed: {exc}")
        return {"error": str(exc)}, 500


# ---------------------------------------------------------------------------
# Self-Hosted TTS (XTTS v2) endpoints
# ---------------------------------------------------------------------------

@app.get("/local-tts/status")
def local_tts_status():
    """Check which TTS backends are installed."""
    backends = available_backends()
    return {"available": local_tts_available(), "backends": backends}


@app.get("/local-tts/voices")
def local_tts_voices():
    """List voice samples in the voice_samples directory (for XTTS cloning)."""
    samples = list_voice_samples(settings.voice_samples_dir)
    return {"status": "ok", "voices": samples}


@app.get("/local-tts/edge-voices")
def local_tts_edge_voices():
    """List available Edge-TTS voices (Microsoft free cloud)."""
    try:
        voices = fetch_edge_voices()
        return {"status": "ok", "voices": voices}
    except Exception as exc:
        return {"error": str(exc)}, 500


def _convert_to_wav(src: Path, dst: Path) -> bool:
    """Convert an audio file to WAV using ffmpeg (bundled via imageio-ffmpeg)."""
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return False
    import subprocess
    try:
        subprocess.run(
            [ffmpeg_exe, "-y", "-i", str(src), "-ar", "22050", "-ac", "1", str(dst)],
            capture_output=True, timeout=30, check=True,
        )
        return dst.exists() and dst.stat().st_size > 0
    except Exception as exc:
        print(f"[LocalTTS] ffmpeg conversion failed: {exc}")
        return False


@app.post("/local-tts/upload-voice")
def local_tts_upload_voice():
    """Upload a voice sample WAV/MP3 to voice_samples/."""
    file = request.files.get("voice_file")
    if not file or not file.filename:
        return {"error": "No voice file uploaded."}, 400

    allowed = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return {"error": f"Unsupported format. Use: {', '.join(allowed)}"}, 400

    voice_name = (request.form.get("voice_name") or "").strip()
    if not voice_name:
        voice_name = Path(file.filename).stem

    settings.voice_samples_dir.mkdir(parents=True, exist_ok=True)

    # Save the uploaded file (possibly in a non-WAV format)
    tmp_name = secure_filename(voice_name + ext)
    if not tmp_name:
        return {"error": "Invalid voice name."}, 400
    dest = settings.voice_samples_dir / tmp_name
    file.save(dest)

    # Auto-convert non-WAV formats to WAV for XTTS compatibility
    if ext != ".wav":
        wav_name = secure_filename(voice_name + ".wav")
        wav_dest = settings.voice_samples_dir / wav_name
        if _convert_to_wav(dest, wav_dest):
            dest.unlink()  # remove original non-wav file
            dest = wav_dest
            tmp_name = wav_name
            print(f"[LocalTTS] Converted {ext} → .wav: {dest}")
        else:
            print(f"[LocalTTS] Conversion to WAV failed, keeping original {ext} file")

    print(f"[LocalTTS] Voice sample saved: {dest} ({dest.stat().st_size} bytes)")
    return {"status": "ok", "filename": tmp_name, "size_kb": round(dest.stat().st_size / 1024, 1)}


@app.post("/local-tts/delete-voice")
def local_tts_delete_voice():
    """Delete a voice sample."""
    filename = (request.form.get("filename") or "").strip()
    if not filename:
        return {"error": "Filename is required."}, 400
    safe = secure_filename(filename)
    target = settings.voice_samples_dir / safe
    if not target.exists():
        return {"error": "Voice sample not found."}, 404
    target.unlink()
    return {"status": "ok"}


@app.post("/local-tts/synthesize")
def local_tts_synthesize():
    """Synthesize text using self-hosted TTS (XTTS voice clone or Edge-TTS)."""
    if not local_tts_available():
        return {"error": "No TTS backend installed. Run: pip install edge-tts"}, 400

    text = (request.form.get("text") or "").strip()
    if not text:
        return {"error": "Text is required."}, 400

    backend = (request.form.get("backend") or "auto").strip().lower()
    language = (request.form.get("language") or settings.local_tts_language).strip()

    # XTTS voice cloning params
    voice_filename = (request.form.get("voice_filename") or "").strip()
    speaker_wav = None
    if voice_filename:
        speaker_wav = settings.voice_samples_dir / secure_filename(voice_filename)
        if not speaker_wav.exists():
            return {"error": f"Voice sample not found: {voice_filename}"}, 404

    # Edge-TTS params
    edge_voice = (request.form.get("edge_voice") or "en-US-GuyNeural").strip()
    edge_rate = (request.form.get("edge_rate") or "+0%").strip()
    edge_pitch = (request.form.get("edge_pitch") or "+0Hz").strip()

    try:
        import tempfile
        tmpdir = Path(tempfile.mkdtemp(prefix="videobuild_localtts_"))
        ext = ".wav" if backend == "xtts" else ".mp3"
        out_path = tmpdir / f"output{ext}"

        synthesize_local(
            text=text,
            out_path=out_path,
            backend=backend,
            speaker_wav=speaker_wav,
            edge_voice=edge_voice,
            edge_rate=edge_rate,
            edge_pitch=edge_pitch,
            language=language,
            model_name=settings.local_tts_model,
            device=settings.local_tts_device,
        )

        audio_bytes = out_path.read_bytes()
        mime = "audio/mpeg" if out_path.suffix == ".mp3" else "audio/wav"

        try:
            out_path.unlink()
            tmpdir.rmdir()
        except Exception:
            pass

        return send_file(
            io.BytesIO(audio_bytes),
            mimetype=mime,
            as_attachment=False,
            download_name=f"local_tts_output{ext}",
        )
    except Exception as exc:
        print(f"[LocalTTS] Synthesis failed: {exc}")
        return {"error": str(exc)}, 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
