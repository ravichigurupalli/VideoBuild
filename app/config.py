from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    project_root: Path
    slides_dir: Path
    audio_file: Path
    output_file: Path
    client_secret_file: Path
    token_file: Path
    video_title_prefix: str
    video_description: str
    video_privacy: str
    default_video_format: str
    seconds_per_image: int
    resolution_width: int
    resolution_height: int
    short_resolution_width: int
    short_resolution_height: int
    bitrate: str
    fps: int
    category_id: str
    audio_volume: float
    bgm_volume: float
    bgm_volume_with_voice: float
    keep_output: bool
    enable_tts: bool
    tts_voice: str | None
    tts_rate: int
    hf_api_token: str | None
    gemini_api_key: str | None
    script_provider: str
    tts_provider: str
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str
    elevenlabs_model_id: str
    elevenlabs_stability: float
    elevenlabs_similarity_boost: float


def load_settings(base_dir: Path | None = None) -> Settings:
    base = base_dir or Path(__file__).resolve().parent.parent
    env_path = base / ".env"
    env_loaded = load_dotenv(env_path)
    if not env_loaded and env_path.exists():
        env_loaded = load_dotenv(env_path, override=True)
    print(f"Loaded .env: {env_loaded} exists={env_path.exists()} size={env_path.stat().st_size if env_path.exists() else 0} from {env_path}")

    def env_bool(key: str, default: str = "false") -> bool:
        raw = os.getenv(key, default)
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    settings = Settings(
        project_root=base,
        slides_dir=base / "assets" / "slides",
        audio_file=base / "assets" / "bgm.mp3",
        output_file=base / os.getenv("OUTPUT_FILENAME", "output.mp4"),
        client_secret_file=base / os.getenv("CLIENT_SECRET_FILE", "client_secret.json"),
        token_file=base / os.getenv("TOKEN_FILE", "token.json"),
        video_title_prefix=os.getenv("VIDEO_TITLE_PREFIX", "Daily Update"),
        video_description=os.getenv("VIDEO_DESCRIPTION", "Automated daily upload"),
        video_privacy=os.getenv("VIDEO_PRIVACY", "private"),
        default_video_format=os.getenv("DEFAULT_VIDEO_FORMAT", "video").strip().lower(),
        seconds_per_image=int(os.getenv("SECONDS_PER_IMAGE", "4")),
        resolution_width=int(os.getenv("RESOLUTION_WIDTH", "1920")),
        resolution_height=int(os.getenv("RESOLUTION_HEIGHT", "1080")),
        short_resolution_width=int(os.getenv("SHORT_RESOLUTION_WIDTH", "1080")),
        short_resolution_height=int(os.getenv("SHORT_RESOLUTION_HEIGHT", "1920")),
        bitrate=os.getenv("BITRATE", "4000k"),
        fps=int(os.getenv("FPS", "30")),
        category_id=os.getenv("CATEGORY_ID", "22"),
        audio_volume=float(os.getenv("AUDIO_VOLUME", "0.6")),
        bgm_volume=float(os.getenv("BGM_VOLUME", "0.35")),
        bgm_volume_with_voice=float(os.getenv("BGM_VOLUME_WITH_VOICE", "0.15")),
        keep_output=env_bool("KEEP_OUTPUT"),
        enable_tts=env_bool("ENABLE_TTS"),
        tts_voice=os.getenv("TTS_VOICE"),
        tts_rate=int(os.getenv("TTS_RATE", "180")),
        hf_api_token=os.getenv("HF_API_TOKEN"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        script_provider=os.getenv("SCRIPT_PROVIDER", "gemini").strip().lower(),
        tts_provider=os.getenv("TTS_PROVIDER", "pyttsx3").strip().lower(),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", "57WpXhyNwaU0uXgMrmDS"),
        elevenlabs_model_id=os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
        elevenlabs_stability=float(os.getenv("ELEVENLABS_STABILITY", "0.45")),
        elevenlabs_similarity_boost=float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.80")),
    )

    print(f"Settings.enable_tts={settings.enable_tts}")
    return settings
