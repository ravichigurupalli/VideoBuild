"""Self-hosted TTS with two backends:

1. **Coqui XTTS v2** — local voice cloning (requires GPU, ~1.8 GB model)
2. **Edge-TTS** — Microsoft free cloud TTS (no API key, many voices, lightweight)
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

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
    """Synthesize speech using Microsoft Edge-TTS (free, no API key)."""
    import edge_tts

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async def _synth():
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(str(out_path))

    print(f"[EdgeTTS] Synthesizing {len(text)} chars, voice={voice}")
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
