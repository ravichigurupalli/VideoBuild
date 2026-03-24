from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import wave
from typing import Iterable

import numpy as np
from moviepy.editor import AudioFileClip, CompositeAudioClip, ImageClip, afx, concatenate_videoclips
from vosk import KaldiRecognizer, Model

# Pillow >=10 removed Image.ANTIALIAS; alias it for MoviePy compatibility
try:  # pragma: no cover - defensive compatibility
    from PIL import Image  # type: ignore
    from PIL import ImageDraw, ImageFont  # type: ignore

    if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except Exception:
    pass

from .config import Settings
from .tts import synthesize_to_file


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return (255, 255, 255)
    return tuple(int(value[index:index + 2], 16) for index in range(0, 6, 2))


def _normalize_word(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def _extract_word_timings(audio_path: Path, model_path: Path) -> list[dict[str, float | str]]:
    if not model_path.exists():
        print(f"Caption timing model not found: {model_path}")
        return []

    words: list[dict[str, float | str]] = []
    with wave.open(str(audio_path), "rb") as wav_file:
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2 or wav_file.getcomptype() != "NONE":
            raise ValueError("TTS audio must be mono PCM WAV for Vosk caption timing")

        model = Model(str(model_path))
        recognizer = KaldiRecognizer(model, wav_file.getframerate())
        recognizer.SetWords(True)

        while True:
            data = wav_file.readframes(4000)
            if len(data) == 0:
                break
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                words.extend(result.get("result", []))

        final_result = json.loads(recognizer.FinalResult())
        words.extend(final_result.get("result", []))

    return words


def _align_caption_words(text: str, timed_words: list[dict[str, float | str]], fallback_duration: float) -> list[dict[str, float | str]]:
    source_words = [word for word in text.split() if word.strip()]
    if not source_words:
        return []

    if not timed_words:
        chunk_duration = max(fallback_duration / len(source_words), 0.1)
        return [
            {
                "word": word,
                "start": index * chunk_duration,
                "end": min((index + 1) * chunk_duration, fallback_duration),
            }
            for index, word in enumerate(source_words)
        ]

    aligned: list[dict[str, float | str]] = []
    timed_index = 0
    for word in source_words:
        normalized_source = _normalize_word(word)
        matched = None
        while timed_index < len(timed_words):
            candidate = timed_words[timed_index]
            timed_index += 1
            normalized_candidate = _normalize_word(str(candidate.get("word", "")))
            if not normalized_source or normalized_candidate == normalized_source:
                matched = candidate
                break
        if matched:
            aligned.append(
                {
                    "word": word,
                    "start": float(matched.get("start", 0.0)),
                    "end": float(matched.get("end", 0.0)),
                }
            )

    if aligned:
        return aligned

    chunk_duration = max(fallback_duration / len(source_words), 0.1)
    return [
        {
            "word": word,
            "start": index * chunk_duration,
            "end": min((index + 1) * chunk_duration, fallback_duration),
        }
        for index, word in enumerate(source_words)
    ]


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


def _caption_chunks(text: str, words_per_chunk: int) -> list[str]:
    words = [word for word in text.split() if word.strip()]
    if not words:
        return []
    chunk_size = max(words_per_chunk, 1)
    return [" ".join(words[index:index + chunk_size]) for index in range(0, len(words), chunk_size)]


def _load_caption_font(font_size: int):
    for font_name in ["arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(font_name, font_size)
        except Exception:
            continue
    return ImageFont.load_default()


def _measure_text(draw: ImageDraw.ImageDraw, text: str, font, stroke_width: int) -> tuple[int, int]:
    if not text:
        return (0, 0)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return right - left, bottom - top


def _group_caption_words(words: list[dict[str, float | str]], words_per_chunk: int) -> list[list[dict[str, float | str]]]:
    chunk_size = max(words_per_chunk, 1)
    return [words[index:index + chunk_size] for index in range(0, len(words), chunk_size)]


def _caption_position(resolution: tuple[int, int], overlay_size: tuple[int, int], position_y: float) -> tuple[int, int]:
    video_width, video_height = resolution
    overlay_width, overlay_height = overlay_size
    x = max(int((video_width - overlay_width) / 2), 0)
    y = max(int(video_height * position_y - overlay_height / 2), 0)
    return (x, y)


def _active_word_index(words: list[dict[str, float | str]], current_time: float) -> int:
    for index, word in enumerate(words):
        start = float(word["start"])
        end = float(word["end"])
        if start <= current_time < end:
            return index
    return -1


def _make_caption_image(
    words: list[dict[str, float | str]],
    active_index: int,
    resolution: tuple[int, int],
    font_size: int,
    position_y: float,
    highlight_color: tuple[int, int, int],
    inactive_color: tuple[int, int, int],
    stroke_color: tuple[int, int, int],
    scale: float = 1.0,
) -> tuple[np.ndarray, tuple[int, int]]:
    width, height = resolution
    scaled_font_size = max(int(font_size * scale), 24)
    font = _load_caption_font(scaled_font_size)
    max_text_width = int(width * 0.82)
    measure_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(measure_image)
    lines: list[str] = []
    line_word_groups: list[list[tuple[int, str]]] = []
    current_line = ""
    current_group: list[tuple[int, str]] = []

    for index, word_data in enumerate(words):
        word = str(word_data["word"])
        candidate = word if not current_line else f"{current_line} {word}"
        candidate_width, _ = _measure_text(draw, candidate, font, 4)
        if candidate_width <= max_text_width:
            current_line = candidate
            current_group.append((index, word))
        else:
            if current_line:
                lines.append(current_line)
                line_word_groups.append(current_group)
            current_line = word
            current_group = [(index, word)]

    if current_line:
        lines.append(current_line)
        line_word_groups.append(current_group)

    line_spacing = max(int(scaled_font_size * 0.25), 12)
    line_heights: list[int] = []
    line_widths: list[int] = []
    for line in lines:
        line_width, line_height = _measure_text(draw, line, font, 4)
        line_widths.append(line_width)
        line_heights.append(line_height)

    block_height = sum(line_heights) + max(len(lines) - 1, 0) * line_spacing

    padding_x = 36
    padding_y = 24
    max_line_width = max(line_widths) if line_widths else 0
    box_width = max_line_width + padding_x * 2
    box_height = block_height + padding_y * 2
    canvas = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    box_x1 = 0
    box_y1 = 0
    box_x2 = box_width
    box_y2 = box_height
    draw.rounded_rectangle((box_x1, box_y1, box_x2, box_y2), radius=24, fill=(0, 0, 0, 140))

    current_y = padding_y
    for line_index, line in enumerate(lines):
        line_width = line_widths[line_index]
        x = int((box_width - line_width) / 2)
        cursor_x = x
        word_group = line_word_groups[line_index]
        for word_index, word in word_group:
            fill_color = highlight_color if word_index == active_index else inactive_color
            word_width, _ = _measure_text(draw, word, font, 4)
            draw.text(
                (cursor_x, current_y),
                word,
                font=font,
                fill=(*fill_color, 255),
                stroke_width=4,
                stroke_fill=(*stroke_color, 255),
            )
            space_width, _ = _measure_text(draw, " ", font, 4)
            cursor_x += word_width + space_width
        current_y += line_heights[line_index] + line_spacing

    return np.array(canvas), _caption_position(resolution, (box_width, box_height), position_y)


def _build_caption_groups(
    settings: Settings,
    text: str,
    video_duration: float,
    voice_path: Path | None,
) -> list[list[dict[str, float | str]]]:
    timed_words: list[dict[str, float | str]] = []
    if voice_path and voice_path.exists():
        try:
            timed_words = _extract_word_timings(voice_path, settings.caption_vosk_model_path)
        except Exception as exc:
            print(f"Caption timing extraction failed, falling back to estimated timings: {exc}")

    source_words = _align_caption_words(text, timed_words, video_duration)
    if not source_words or video_duration <= 0:
        return []

    return _group_caption_words(source_words, settings.caption_words_per_chunk)


def _find_active_group(groups: list[list[dict[str, float | str]]], current_time: float) -> list[dict[str, float | str]] | None:
    for group in groups:
        group_start = float(group[0]["start"])
        group_end = float(group[-1]["end"])
        if group_start <= current_time <= group_end:
            return group
    return None


def _burn_caption_frame(
    frame: np.ndarray,
    current_time: float,
    groups: list[list[dict[str, float | str]]],
    settings: Settings,
    resolution: tuple[int, int],
) -> np.ndarray:
    group = _find_active_group(groups, current_time)
    if not group:
        return frame

    highlight_color = _hex_to_rgb(settings.caption_highlight_color)
    inactive_color = _hex_to_rgb(settings.caption_inactive_color)
    stroke_color = _hex_to_rgb(settings.caption_stroke_color)
    active_index = _active_word_index(group, current_time)
    scale = settings.caption_pop_scale if active_index >= 0 else 1.0
    caption_image, caption_position = _make_caption_image(
        group,
        active_index,
        resolution,
        settings.caption_font_size,
        settings.caption_position_y,
        highlight_color,
        inactive_color,
        stroke_color,
        scale=scale,
    )

    x, y = caption_position
    overlay_height, overlay_width = caption_image.shape[:2]
    frame_height, frame_width = frame.shape[:2]
    x2 = min(x + overlay_width, frame_width)
    y2 = min(y + overlay_height, frame_height)
    if x >= x2 or y >= y2:
        return frame

    overlay = caption_image[: y2 - y, : x2 - x]
    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
    base_region = frame[y:y2, x:x2].astype(np.float32)
    overlay_rgb = overlay[:, :, :3].astype(np.float32)
    frame[y:y2, x:x2] = (alpha * overlay_rgb + (1.0 - alpha) * base_region).astype(np.uint8)
    return frame


def build_slideshow(settings: Settings, image_paths: Iterable[Path], narration: str | None = None) -> Path:
    image_list = sorted(
        [p for p in image_paths if p.is_file()],
        key=lambda path: natural_sort_key(path.name),
    )
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

    video = concatenate_videoclips(clips, method="chain")
    if target_video_duration > 0:
        video = video.set_duration(target_video_duration)

    if settings.enable_captions and narration and narration.strip():
        caption_groups = _build_caption_groups(settings, narration, video.duration, voice_path)
        if caption_groups:
            video = video.fl(lambda gf, t: _burn_caption_frame(gf(t), t, caption_groups, settings, resolution))
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
