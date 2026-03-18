from __future__ import annotations

import tempfile
from pathlib import Path
import pyttsx3

from .config import Settings


def synthesize_to_file(settings: Settings, text: str) -> Path:
    """Generate narration to a WAV file using local system voices."""
    if not text.strip():
        raise ValueError("TTS text is empty")

    engine = pyttsx3.init()
    if settings.tts_voice:
        try:
            engine.setProperty("voice", settings.tts_voice)
        except Exception:
            pass
    engine.setProperty("rate", settings.tts_rate)

    tmpdir = Path(tempfile.mkdtemp(prefix="videobuild_tts_"))
    out_path = tmpdir / "voice.wav"  # pyttsx3 outputs WAV reliably
    engine.save_to_file(text, str(out_path))
    engine.runAndWait()

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("TTS audio file was not created")

    return out_path
