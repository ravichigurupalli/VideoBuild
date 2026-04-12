from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Iterable

from moviepy.editor import AudioFileClip, CompositeAudioClip, ImageClip, afx, concatenate_videoclips

# Pillow >=10 removed Image.ANTIALIAS; alias it for MoviePy compatibility
try:  # pragma: no cover - defensive compatibility
    from PIL import Image  # type: ignore

    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except Exception:
    pass

from .config import Settings
from .tts import synthesize_to_file


def natural_sort_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


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


def _resolve_output_resolution(settings: Settings, video_format: str) -> tuple[int, int]:
    if video_format == "short":
        return (settings.short_resolution_width, settings.short_resolution_height)
    return (settings.resolution_width, settings.resolution_height)


def build_slideshow(
    settings: Settings,
    image_paths: Iterable[Path],
    narration: str | None = None,
    video_format: str = "video",
    tts_provider: str | None = None,
    voice_id: str | None = None,
) -> Path:
    image_list = sorted(
        [p for p in image_paths if p.is_file()],
        key=lambda path: natural_sort_key(path.name),
    )
    print("Sorted image list:", [p.name for p in image_list])
    if not image_list:
        raise FileNotFoundError(f"No images found in {settings.slides_dir}")

    voice_path: Path | None = None
    if settings.enable_tts and narration:
        print(f"TTS enabled, narration length={len(narration)}")
        voice_path = synthesize_to_file(settings, narration, tts_provider=tts_provider, voice_id=voice_id)
        print(f"TTS file: {voice_path}")
    else:
        print("TTS disabled or no narration text")
        print(f"enable_tts: {settings.enable_tts}")
        print(f"narration: {narration}")
    voice_audio: AudioFileClip | None = None
    bgm_audio: AudioFileClip | None = None

    if voice_path and voice_path.exists():
        print(f"Attaching TTS audio: {voice_path} size={voice_path.stat().st_size}")
        voice_audio = AudioFileClip(str(voice_path)).volumex(settings.audio_volume)

    base_video_duration = len(image_list) * settings.seconds_per_image
    target_video_duration = max(base_video_duration, voice_audio.duration if voice_audio else 0)
    per_image_duration = max(target_video_duration / len(image_list), 0.1)
    print(
        f"Image timeline: count={len(image_list)} per_image_duration={per_image_duration:.2f}s total={target_video_duration:.2f}s"
    )

    resolution = _resolve_output_resolution(settings, video_format)
    print(f"Building format={video_format} resolution={resolution[0]}x{resolution[1]}")
    clips = []
    for i, img_path in enumerate(image_list):
        duration = per_image_duration if i < len(image_list) - 1 else max(per_image_duration - 2, 0.1)
        clip = ImageClip(str(img_path)).set_duration(duration)
        clip = _fit_image_clip(clip, resolution)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="chain")

    if settings.audio_file.exists():
        print(f"Using bgm audio: {settings.audio_file}")
        bgm_audio = AudioFileClip(str(settings.audio_file)).fx(afx.audio_loop, duration=video.duration)
        bgm_level = settings.bgm_volume_with_voice if voice_audio else settings.bgm_volume
        bgm_audio = bgm_audio.volumex(bgm_level)

    if voice_audio and bgm_audio:
        video = video.set_audio(CompositeAudioClip([bgm_audio, voice_audio]))
    elif voice_audio:
        video = video.set_audio(voice_audio)
    elif bgm_audio:
        video = video.set_audio(bgm_audio)

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
