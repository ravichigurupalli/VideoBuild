"""Self-hosted TTS with two backends:

1. **Coqui XTTS v2** — local voice cloning (requires GPU, ~1.8 GB model)
2. **Edge-TTS** — Microsoft free cloud TTS (no API key, many voices, lightweight)
"""
from __future__ import annotations

import asyncio
import html
import re
import threading
from pathlib import Path
from typing import List, Tuple
import sys

# ---------------------------------------------------------------------------
# CRITICAL: Monkeypatch transformers BEFORE ANY COQUI IMPORT
# ---------------------------------------------------------------------------
# Coqui-TTS has compatibility issues with newer transformers on Windows.
# We inject the missing function before TTS tries to import it.

try:
    import torch
    # Create a fake isin_mps_friendly if it doesn't exist
    import transformers.pytorch_utils as pu_module
    if not hasattr(pu_module, 'isin_mps_friendly'):
        pu_module.isin_mps_friendly = torch.isin
except Exception:
    pass

# Also patch sys.modules to prevent re-import issues
try:
    import torch
    class PatchedPyTorchUtils:
        def __getattr__(self, name):
            if name == 'isin_mps_friendly':
                return torch.isin
            import transformers.pytorch_utils as pu
            return getattr(pu, name)
    
    if 'transformers.pytorch_utils' not in sys.modules or not hasattr(sys.modules.get('transformers.pytorch_utils'), 'isin_mps_friendly'):
        import transformers.pytorch_utils as pu
        if not hasattr(pu, 'isin_mps_friendly'):
            pu.isin_mps_friendly = torch.isin
except Exception:
    pass
# ---------------------------------------------------------------------------
# Tone marker → SSML prosody mapping
# ---------------------------------------------------------------------------

# Keys are lowercased, stripped. Values: (rate, pitch, volume)
TONE_MAP: dict[str, tuple[str, str, str]] = {
    "slow, dramatic tone":     ("x-slow", "-4st",  "medium"),
    "slow, dramatic":          ("x-slow", "-4st",  "medium"),
    "clear, explanatory tone": ("medium",  "+0st",  "loud"),
    "clear, explanatory":      ("medium",  "+0st",  "loud"),
    "curious tone":            ("medium",  "+3st",  "medium"),
    "curious":                 ("medium",  "+3st",  "medium"),
    "serious tone":            ("slow",    "-2st",  "medium"),
    "serious":                 ("slow",    "-2st",  "medium"),
    "tense tone":              ("fast",    "+2st",  "loud"),
    "tense":                   ("fast",    "+2st",  "loud"),
    "quiet tone":              ("slow",    "-1st",  "soft"),
    "quiet":                   ("slow",    "-1st",  "soft"),
    "analytical tone":         ("medium",  "-1st",  "medium"),
    "analytical":              ("medium",  "-1st",  "medium"),
    "measured tone":           ("slow",    "+0st",  "medium"),
    "measured":                ("slow",    "+0st",  "medium"),
    "slow, cinematic":         ("x-slow",  "-5st",  "soft"),
    "cinematic":               ("x-slow",  "-5st",  "soft"),
}

_TONE_PATTERN = re.compile(r'\(([^)]+)\)')


def _resolve_tone(marker_text: str, default_rate: str, default_pitch: str) -> tuple[str, str, str]:
    """Map a marker string to (rate, pitch, volume). Falls back to defaults."""
    key = marker_text.strip().lower()
    if key in TONE_MAP:
        return TONE_MAP[key]
    # Try partial match — e.g. 'slow dramatic' matches 'slow, dramatic tone'
    for tone_key, params in TONE_MAP.items():
        if all(word in key for word in re.split(r'[,\s]+', tone_key) if len(word) > 2):
            return params
    return (default_rate, default_pitch, "medium")


def _has_tone_markers(text: str) -> bool:
    """Return True if the text contains at least one recognisable tone marker."""
    for m in _TONE_PATTERN.finditer(text):
        key = m.group(1).strip().lower()
        if key in TONE_MAP:
            return True
        for tone_key in TONE_MAP:
            if all(word in key for word in re.split(r'[,\s]+', tone_key) if len(word) > 2):
                return True
    return False


def _parse_tone_markers(
    text: str,
    default_rate: str = "+0%",
    default_pitch: str = "+0Hz",
) -> List[Tuple[str, str, str, str]]:
    """Split text on tone markers and return list of (segment_text, rate, pitch, volume)."""
    segments: List[Tuple[str, str, str, str]] = []
    current_rate, current_pitch, current_vol = default_rate, default_pitch, "medium"
    last_end = 0

    for m in _TONE_PATTERN.finditer(text):
        # Text before this marker
        segment = text[last_end:m.start()].strip()
        if segment:
            segments.append((segment, current_rate, current_pitch, current_vol))

        # Resolve new tone
        current_rate, current_pitch, current_vol = _resolve_tone(
            m.group(1), default_rate, default_pitch
        )
        last_end = m.end()

    # Remaining text after last marker
    tail = text[last_end:].strip()
    if tail:
        segments.append((tail, current_rate, current_pitch, current_vol))

    return segments


def _build_ssml(
    segments: List[Tuple[str, str, str, str]],
    voice: str,
) -> str:
    """Build an SSML document from (text, rate, pitch, volume) segments."""
    parts = [
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US">',
        f'<voice name="{html.escape(voice)}">',
    ]
    for seg_text, rate, pitch, volume in segments:
        safe = html.escape(seg_text)
        parts.append(
            f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}">'
            f'{safe}</prosody>'
        )
    parts.append('</voice></speak>')
    return ''.join(parts)


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def _patch_transformers():
    """Patch missing isin_mps_friendly and torchcodec availability in transformers
    for coqui-tts compatibility on Windows (no FFmpeg shared DLLs)."""
    try:
        import transformers.pytorch_utils as pu
        if not hasattr(pu, "isin_mps_friendly"):
            import torch
            pu.isin_mps_friendly = torch.isin
    except Exception:
        pass
    # Replace torchaudio.load with soundfile-based loader on Windows.
    # torchaudio 2.9+ ignores the backend param and always uses torchcodec,
    # which needs FFmpeg shared DLLs not available on Windows by default.
    try:
        import torchaudio
        import torch
        import soundfile as sf
        import numpy as np

        def _sf_load(filepath, frame_offset=0, num_frames=-1, normalize=True,
                     channels_first=True, format=None, buffer_size=4096, backend=None):
            data, sample_rate = sf.read(str(filepath), dtype="float32",
                                        start=frame_offset,
                                        stop=frame_offset + num_frames if num_frames > 0 else None,
                                        always_2d=True)
            # data shape: (frames, channels) → convert to tensor
            waveform = torch.from_numpy(data)
            if channels_first:
                waveform = waveform.T  # (channels, frames)
            return waveform, sample_rate

        torchaudio.load = _sf_load
    except ImportError:
        pass


def _accept_coqui_tos():
    """Auto-accept Coqui TOS and bypass SSL for model download (corporate proxy)."""
    import os, ssl
    os.environ["COQUI_TOS_AGREED"] = "1"
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except AttributeError:
        pass


def _has_coqui() -> bool:
    try:
        _patch_transformers()
        import TTS  # noqa: F401
        return True
    except ImportError:
        return False


def _has_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def available_backends() -> dict:
    """Return which TTS backends are installed."""
    return {
        "xtts": _has_coqui(),
        "edge": _has_edge_tts(),
    }


def is_available() -> bool:
    """True if at least one self-hosted backend is installed."""
    return _has_coqui() or _has_edge_tts()


# ---------------------------------------------------------------------------
# Backend 1: Coqui XTTS v2 (local voice cloning)
# ---------------------------------------------------------------------------

_tts_instance = None
_tts_lock = threading.Lock()


def _get_tts(model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2", device: str = "auto"):
    """Lazy-load the XTTS model (heavy, ~1.8 GB download on first run)."""
    global _tts_instance
    if _tts_instance is not None:
        return _tts_instance

    with _tts_lock:
        if _tts_instance is not None:
            return _tts_instance

        _accept_coqui_tos()
        import torch
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # Monkey-patch requests to skip SSL verification for model download
        import requests as _req
        _orig_get = _req.Session.send
        def _patched_send(self, request, **kwargs):
            kwargs["verify"] = False
            return _orig_get(self, request, **kwargs)
        _req.Session.send = _patched_send
        from TTS.api import TTS

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        print(f"[XTTS] Loading model {model_name} on {device} ...")
        _tts_instance = TTS(model_name).to(device)
        # Restore original requests behavior
        _req.Session.send = _orig_get
        print(f"[XTTS] Model loaded successfully on {device}")
        return _tts_instance


def synthesize_xtts(
    text: str,
    out_path: Path,
    speaker_wav: str | Path,
    language: str = "en",
    model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
    device: str = "auto",
) -> Path:
    """Synthesize speech using XTTS v2 with voice cloning."""
    speaker_wav = Path(speaker_wav)
    if not speaker_wav.exists():
        raise FileNotFoundError(f"Voice sample not found: {speaker_wav}")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tts = _get_tts(model_name=model_name, device=device)

    print(f"[XTTS] Synthesizing {len(text)} chars, speaker={speaker_wav.name}, lang={language}")
    tts.tts_to_file(
        text=text,
        speaker_wav=str(speaker_wav),
        language=language,
        file_path=str(out_path),
    )

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("XTTS: output audio file was not created")

    print(f"[XTTS] Output: {out_path} ({out_path.stat().st_size} bytes)")
    return out_path


# ---------------------------------------------------------------------------
# Backend 2: Edge-TTS (Microsoft free cloud TTS)
# ---------------------------------------------------------------------------

_edge_voices_cache: list[dict] | None = None


def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def fetch_edge_voices() -> list[dict]:
    """Fetch list of available Edge-TTS voices."""
    global _edge_voices_cache
    if _edge_voices_cache is not None:
        return _edge_voices_cache

    import edge_tts

    async def _fetch():
        return await edge_tts.list_voices()

    raw_voices = _run_async(_fetch())
    voices = []
    for v in raw_voices:
        voices.append({
            "voice_id": v["ShortName"],
            "name": v.get("FriendlyName", v["ShortName"]),
            "short_name": v["ShortName"],
            "gender": v.get("Gender", ""),
            "locale": v.get("Locale", ""),
        })
    _edge_voices_cache = voices
    return voices


def synthesize_edge(
    text: str,
    out_path: Path,
    voice: str = "en-US-GuyNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Path:
    """Synthesize speech using Microsoft Edge-TTS (free, no API key).

    If the text contains inline tone markers such as (slow, dramatic tone) or
    (tense tone), the text is split into segments and each segment is rendered
    with the corresponding SSML prosody settings (rate/pitch/volume). Markers
    are removed from the spoken output.
    """
    import edge_tts

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if _has_tone_markers(text):
        segments = _parse_tone_markers(text, default_rate=rate, default_pitch=pitch)
        ssml = _build_ssml(segments, voice)
        print(f"[EdgeTTS] Tone markers detected — using SSML ({len(segments)} segments), voice={voice}")
        async def _synth_ssml():
            communicate = edge_tts.Communicate(ssml, voice)
            await communicate.save(str(out_path))
        _run_async(_synth_ssml())
    else:
        print(f"[EdgeTTS] Synthesizing {len(text)} chars, voice={voice}")
        async def _synth():
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await communicate.save(str(out_path))
        _run_async(_synth())

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Edge-TTS: output audio file was not created")

    print(f"[EdgeTTS] Output: {out_path} ({out_path.stat().st_size} bytes)")
    return out_path


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

def synthesize_local(
    text: str,
    out_path: Path,
    backend: str = "auto",
    speaker_wav: str | Path | None = None,
    edge_voice: str = "en-US-GuyNeural",
    edge_rate: str = "+0%",
    edge_pitch: str = "+0Hz",
    language: str = "en",
    model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
    device: str = "auto",
) -> Path:
    """Synthesize speech using the best available local/self-hosted backend.

    backend: "xtts", "edge", or "auto" (tries xtts first if speaker_wav given).
    """
    if backend == "auto":
        if speaker_wav and _has_coqui():
            backend = "xtts"
        elif _has_edge_tts():
            backend = "edge"
        elif _has_coqui():
            backend = "xtts"
        else:
            raise RuntimeError("No self-hosted TTS backend installed. Run: pip install edge-tts")

    if backend == "xtts":
        if not _has_coqui():
            raise RuntimeError("coqui-tts not installed. Run: pip install coqui-tts")
        if not speaker_wav:
            raise ValueError("XTTS requires a speaker_wav voice sample for cloning.")
        return synthesize_xtts(text, out_path, speaker_wav, language, model_name, device)

    if backend == "edge":
        if not _has_edge_tts():
            raise RuntimeError("edge-tts not installed. Run: pip install edge-tts")
        return synthesize_edge(text, out_path, voice=edge_voice, rate=edge_rate, pitch=edge_pitch)

    raise ValueError(f"Unknown TTS backend: {backend}")


# ---------------------------------------------------------------------------
# Voice sample management
# ---------------------------------------------------------------------------

def list_voice_samples(voice_samples_dir: Path) -> list[dict]:
    """List available voice sample files from the voice_samples directory."""
    voice_samples_dir = Path(voice_samples_dir)
    if not voice_samples_dir.exists():
        return []

    samples = []
    for f in sorted(voice_samples_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"}:
            samples.append({
                "name": f.stem,
                "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
    return samples
