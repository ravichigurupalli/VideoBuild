from __future__ import annotations

from datetime import date
from pathlib import Path
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

    voice_path: Path | None = None
    if settings.enable_tts and narration:
        print(f"TTS enabled, narration length={len(narration)}")
        voice_path = synthesize_to_file(settings, narration)
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

    resolution = (settings.resolution_width, settings.resolution_height)
    clips = []
    for img_path in sorted(image_list):
        clip = ImageClip(str(img_path)).set_duration(per_image_duration)
        clip = _fit_image_clip(clip, resolution)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    if target_video_duration > 0:
        video = video.set_duration(target_video_duration)

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
