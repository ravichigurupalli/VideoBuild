from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

# Pillow >=10 removed Image.ANTIALIAS; alias it for MoviePy compatibility
try:  # pragma: no cover - defensive compatibility
    from PIL import Image  # type: ignore

    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except Exception:
    pass

from .config import Settings
from .tts import synthesize_to_file


def _fit_image_clip(clip: ImageClip, resolution: tuple[int, int]) -> ImageClip:
    target_width, target_height = resolution
    clip_width, clip_height = clip.size

    clip_ratio = clip_width / clip_height
    target_ratio = target_width / target_height

    if clip_ratio > target_ratio:
        clip = clip.resize(height=target_height)
    else:
        clip = clip.resize(width=target_width)

    return clip.crop(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=target_width,
        height=target_height,
    )


def build_slideshow(settings: Settings, image_paths: Iterable[Path], narration: str | None = None) -> Path:
    image_list = [p for p in image_paths if p.is_file()]
    if not image_list:
        raise FileNotFoundError(f"No images found in {settings.slides_dir}")

    resolution = (settings.resolution_width, settings.resolution_height)
    clips = []
    for img_path in sorted(image_list):
        clip = ImageClip(str(img_path)).set_duration(settings.seconds_per_image)
        clip = _fit_image_clip(clip, resolution)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")

    voice_path: Path | None = None
    if settings.enable_tts and narration:
        print(f"TTS enabled, narration length={len(narration)}")
        voice_path = synthesize_to_file(settings, narration)
        print(f"TTS file: {voice_path}")
    else:
        print("TTS disabled or no narration text")
        print(f"enable_tts: {settings.enable_tts}")
        print(f"narration: {narration}")
    if voice_path and voice_path.exists():
        print(f"Attaching TTS audio: {voice_path} size={voice_path.stat().st_size}")
        audio = AudioFileClip(str(voice_path)).volumex(settings.audio_volume)
        video = video.set_audio(audio)
    elif settings.audio_file.exists():
        print(f"Using bgm audio: {settings.audio_file}")
        audio = AudioFileClip(str(settings.audio_file)).volumex(settings.audio_volume)
        video = video.set_audio(audio)

    output_path = settings.output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video.write_videofile(
        str(output_path),
        fps=settings.fps,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        bitrate=settings.bitrate,
    )

    # clean up temp TTS folder after write
    if voice_path:
        try:
            if voice_path.exists():
                voice_path.unlink()
            if voice_path.parent.exists():
                voice_path.parent.rmdir()
        except Exception:
            pass

    return output_path


def default_title(settings: Settings) -> str:
    return f"{settings.video_title_prefix} {date.today().isoformat()}"
