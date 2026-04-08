from __future__ import annotations

import random
import re
import tempfile
from pathlib import Path

import numpy as np
import requests
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
)

VIDEO_STYLES = ("static", "animated")

try:
    from PIL import Image

    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass

from .config import Settings
from .image_gen import generate_image
from .tts import synthesize_to_file
from .video_builder import _fit_image_clip, _resolve_output_resolution


# ---------------------------------------------------------------------------
# Scene splitting
# ---------------------------------------------------------------------------

def _split_into_scenes(text: str, max_sentences_per_scene: int = 2) -> list[dict]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return []

    scenes: list[dict] = []
    for i in range(0, len(sentences), max_sentences_per_scene):
        chunk = sentences[i : i + max_sentences_per_scene]
        narration = " ".join(chunk)
        visual_prompt = (
            f"cinematic high quality illustration of: {narration}, "
            "vibrant colors, detailed, 4K, professional photography"
        )
        scenes.append({"narration": narration, "visual_prompt": visual_prompt})

    return scenes


# ---------------------------------------------------------------------------
# Video clip generation via HuggingFace
# ---------------------------------------------------------------------------

HF_VIDEO_API_URL = (
    "https://router.huggingface.co/hf-inference/models/"
    "ali-vilab/text-to-video-ms-1.7b"
)


def _generate_video_clip(
    prompt: str,
    hf_token: str,
    output_path: Path,
    timeout: int = 180,
) -> Path | None:
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {
        "inputs": prompt,
        "options": {"wait_for_model": True},
    }

    print(f"  Generating video clip: {prompt[:80]}...")
    try:
        response = requests.post(
            HF_VIDEO_API_URL, headers=headers, json=payload, timeout=timeout
        )
        if response.status_code != 200:
            print(f"  Video clip API error ({response.status_code}): {response.text[:200]}")
            return None

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        print(f"  Video clip saved: {output_path}")
        return output_path
    except Exception as exc:
        print(f"  Video clip generation failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Resize / fit a video clip to target resolution
# ---------------------------------------------------------------------------

def _fit_video_clip(clip: VideoFileClip, resolution: tuple[int, int]) -> VideoFileClip:
    target_w, target_h = resolution
    clip_w, clip_h = clip.size
    clip_ratio = clip_w / clip_h
    target_ratio = target_w / target_h

    if clip_ratio > target_ratio:
        clip = clip.resize(height=target_h)
    else:
        clip = clip.resize(width=target_w)

    return clip.crop(
        x_center=clip.w / 2,
        y_center=clip.h / 2,
        width=target_w,
        height=target_h,
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Animation helpers
# ---------------------------------------------------------------------------

def _ken_burns_clip(
    img_path: str,
    duration: float,
    resolution: tuple[int, int],
    fps: int = 30,
) -> VideoFileClip:
    """Create a smooth pan/zoom (Ken Burns) clip from a static image."""
    target_w, target_h = resolution
    img = Image.open(img_path).convert("RGB")

    # Work at 1.3x resolution so we have room to pan/zoom
    canvas_w = int(target_w * 1.3)
    canvas_h = int(target_h * 1.3)
    img_ratio = img.width / img.height
    canvas_ratio = canvas_w / canvas_h
    if img_ratio > canvas_ratio:
        img = img.resize((canvas_w, int(canvas_w / img_ratio)), Image.LANCZOS)
    else:
        img = img.resize((int(canvas_h * img_ratio), canvas_h), Image.LANCZOS)
    # Center-pad to canvas size
    padded = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
    padded.paste(img, ((canvas_w - img.width) // 2, (canvas_h - img.height) // 2))
    img_array = np.array(padded)

    # Random pan/zoom direction
    effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
    effect = random.choice(effects)

    def make_frame(t):
        progress = t / max(duration, 0.01)
        if effect == "zoom_in":
            scale = 1.0 + 0.2 * progress
        elif effect == "zoom_out":
            scale = 1.2 - 0.2 * progress
        elif effect == "pan_left":
            scale = 1.15
        else:  # pan_right
            scale = 1.15

        crop_w = int(target_w / scale)
        crop_h = int(target_h / scale)
        max_x = canvas_w - crop_w
        max_y = canvas_h - crop_h
        cx = max_x // 2
        cy = max_y // 2

        if effect == "pan_left":
            cx = int(max_x * (1.0 - progress))
        elif effect == "pan_right":
            cx = int(max_x * progress)

        cx = max(0, min(cx, max_x))
        cy = max(0, min(cy, max_y))
        cropped = img_array[cy : cy + crop_h, cx : cx + crop_w]
        # Resize to target resolution
        pil_crop = Image.fromarray(cropped).resize((target_w, target_h), Image.LANCZOS)
        return np.array(pil_crop)

    from moviepy.video.VideoClip import VideoClip
    clip = VideoClip(make_frame, duration=duration)
    clip.fps = fps
    return clip


def _subtitle_clip(
    text: str,
    duration: float,
    resolution: tuple[int, int],
) -> TextClip | None:
    """Create a subtitle overlay clip."""
    target_w, target_h = resolution
    try:
        txt = TextClip(
            text,
            fontsize=int(target_h * 0.035),
            color="white",
            font="Arial",
            stroke_color="black",
            stroke_width=2,
            method="caption",
            size=(int(target_w * 0.85), None),
            align="center",
        )
        txt = txt.set_duration(duration)
        txt = txt.set_position(("center", 0.85), relative=True)
        return txt
    except Exception as exc:
        print(f"  Subtitle creation failed (non-fatal): {exc}")
        return None


def _apply_crossfade(clips: list, fade_duration: float = 0.5) -> list:
    """Offset clips and add crossfade transitions."""
    if len(clips) < 2 or fade_duration <= 0:
        return clips
    faded = []
    for i, clip in enumerate(clips):
        c = clip.crossfadein(fade_duration) if i > 0 else clip
        c = c.crossfadeout(fade_duration) if i < len(clips) - 1 else c
        faded.append(c)
    return faded


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def text_to_video(
    settings: Settings,
    text: str,
    video_format: str = "video",
    video_style: str = "static",
    on_progress: callable | None = None,
) -> Path:
    scenes = _split_into_scenes(text)
    if not scenes:
        raise ValueError("Could not extract any scenes from the provided text.")

    total_steps = len(scenes) * 2 + 2  # image + video per scene, then TTS + stitch
    step = 0

    def progress(msg: str):
        nonlocal step
        step += 1
        print(f"[{step}/{total_steps}] {msg}")
        if on_progress:
            on_progress(step, total_steps, msg)

    resolution = _resolve_output_resolution(settings, video_format)
    hf_token = settings.hf_api_token
    if not hf_token:
        raise ValueError("HF_API_TOKEN not set in .env")

    full_narration = " ".join(scene["narration"] for scene in scenes)

    with tempfile.TemporaryDirectory(prefix="videobuild_t2v_") as tmpdir:
        tmp = Path(tmpdir)
        scene_clips = []

        for idx, scene in enumerate(scenes):
            # --- Generate image for this scene ---
            progress(f"Generating image for scene {idx + 1}/{len(scenes)}")
            img_path = tmp / f"scene_{idx:03d}.png"
            try:
                generate_image(
                    scene["visual_prompt"],
                    hf_token,
                    video_format=video_format,
                    output_path=img_path,
                )
            except Exception as exc:
                print(f"  Image generation failed for scene {idx + 1}: {exc}")
                img_path = None

            # --- Generate video clip for this scene ---
            progress(f"Generating video clip for scene {idx + 1}/{len(scenes)}")
            clip_path = tmp / f"clip_{idx:03d}.mp4"
            video_clip_path = _generate_video_clip(
                scene["visual_prompt"], hf_token, clip_path
            )

            # --- Build the clip for this scene ---
            target_duration = max(len(scene["narration"].split()) * 0.4, 3.0)

            if video_clip_path and video_clip_path.exists():
                try:
                    vclip = VideoFileClip(str(video_clip_path))
                    vclip = _fit_video_clip(vclip, resolution)
                    if vclip.duration < target_duration and img_path and img_path.exists():
                        filler = ImageClip(str(img_path)).set_duration(
                            target_duration - vclip.duration
                        )
                        filler = _fit_image_clip(filler, resolution)
                        combined = concatenate_videoclips(
                            [vclip, filler], method="chain"
                        )
                        scene_clips.append(combined)
                    else:
                        vclip = vclip.set_duration(min(vclip.duration, target_duration))
                        scene_clips.append(vclip)
                    continue
                except Exception as exc:
                    print(f"  Failed to load video clip for scene {idx + 1}: {exc}")

            if img_path and img_path.exists():
                if video_style == "animated":
                    iclip = _ken_burns_clip(
                        str(img_path), target_duration, resolution, fps=settings.fps
                    )
                else:
                    iclip = ImageClip(str(img_path)).set_duration(target_duration)
                    iclip = _fit_image_clip(iclip, resolution)
                if video_style == "animated":
                    sub = _subtitle_clip(scene["narration"], target_duration, resolution)
                    if sub:
                        iclip = CompositeVideoClip([iclip, sub])
                scene_clips.append(iclip)
            else:
                print(f"  WARNING: No visual content for scene {idx + 1}, using black frame")
                from moviepy.editor import ColorClip
                scene_clips.append(
                    ColorClip(resolution, color=(0, 0, 0), duration=target_duration)
                )

        if not scene_clips:
            raise RuntimeError("No scene clips were generated.")

        # --- TTS narration ---
        progress("Generating TTS narration")
        voice_path: Path | None = None
        voice_audio: AudioFileClip | None = None
        if settings.enable_tts and full_narration.strip():
            voice_path = synthesize_to_file(settings, full_narration)
            voice_audio = AudioFileClip(str(voice_path)).volumex(settings.audio_volume)

        # --- Stitch final video ---
        progress("Stitching final video")
        if video_style == "animated" and len(scene_clips) > 1:
            scene_clips = _apply_crossfade(scene_clips, fade_duration=0.5)
        video = concatenate_videoclips(scene_clips, method="compose" if video_style == "animated" else "chain")

        bgm_audio: AudioFileClip | None = None
        if settings.audio_file.exists():
            bgm_audio = AudioFileClip(str(settings.audio_file)).fx(
                afx.audio_loop, duration=video.duration
            )
            bgm_level = (
                settings.bgm_volume_with_voice if voice_audio else settings.bgm_volume
            )
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

        if voice_path:
            try:
                if voice_path.exists():
                    voice_path.unlink()
                if voice_path.parent.exists():
                    voice_path.parent.rmdir()
            except Exception:
                pass

    return output_path
