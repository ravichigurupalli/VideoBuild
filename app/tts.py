from __future__ import annotations

import tempfile
from pathlib import Path

import os

import requests
import urllib3
import pyttsx3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .config import Settings

TTS_PROVIDERS = ("pyttsx3", "elevenlabs", "edge_tts", "xtts")

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"


def _el_session() -> requests.Session:
    """Create a requests session with proxy + SSL settings for ElevenLabs."""
    s = requests.Session()
    s.verify = False
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if proxy:
        s.proxies = {"https": proxy, "http": proxy}
    return s


def _check_proxy_intercept(resp: requests.Response) -> None:
    """Raise a clear error if a corporate proxy intercepted the response."""
    ct = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ct:
        raise RuntimeError(
            "Corporate proxy/firewall is blocking requests to api.elevenlabs.io. "
            "The response was an HTML page instead of JSON. "
            "Ask your IT team to whitelist api.elevenlabs.io, or use a VPN/personal network."
        )


# ---------------------------------------------------------------------------
# Provider: pyttsx3 (local, offline)
# ---------------------------------------------------------------------------

def _synthesize_pyttsx3(settings: Settings, text: str, out_path: Path) -> Path:
    engine = pyttsx3.init()
    if settings.tts_voice:
        try:
            engine.setProperty("voice", settings.tts_voice)
        except Exception:
            pass
    engine.setProperty("rate", settings.tts_rate)
    engine.save_to_file(text, str(out_path))
    engine.runAndWait()
    return out_path


# ---------------------------------------------------------------------------
# Provider: ElevenLabs (cloud API)
# ---------------------------------------------------------------------------

def _synthesize_elevenlabs(
    text: str,
    out_path: Path,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_multilingual_v2",
    stability: float = 0.45,
    similarity_boost: float = 0.80,
) -> Path:
    url = f"{ELEVENLABS_TTS_URL}/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
        },
    }

    print(f"  ElevenLabs TTS: voice={voice_id} model={model_id} stability={stability} similarity={similarity_boost} chars={len(text)}")
    sess = _el_session()
    resp = sess.post(url, json=payload, headers=headers, timeout=120)

    _check_proxy_intercept(resp)

    if resp.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs TTS error ({resp.status_code}): {resp.text[:300]}"
        )

    out_path.write_bytes(resp.content)
    return out_path


FALLBACK_VOICES = [
    {"voice_id": "57WpXhyNwaU0uXgMrmDS", "name": "My Voice (cloned)", "category": "cloned", "labels": {}},
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "category": "premade", "labels": {}},
]


def fetch_elevenlabs_voices(api_key: str) -> list[dict]:
    """Fetch available voices from ElevenLabs API, with fallback for proxy issues."""
    try:
        headers = {"xi-api-key": api_key}
        sess = _el_session()
        resp = sess.get(ELEVENLABS_VOICES_URL, headers=headers, timeout=30)

        _check_proxy_intercept(resp)

        if resp.status_code != 200:
            raise RuntimeError(
                f"ElevenLabs voices error ({resp.status_code}): {resp.text[:300]}"
            )
        voices = resp.json().get("voices", [])
        return [
            {
                "voice_id": v["voice_id"],
                "name": v.get("name", "Unknown"),
                "category": v.get("category", ""),
                "labels": v.get("labels", {}),
            }
            for v in voices
        ]
    except Exception as exc:
        print(f"  ElevenLabs voices API failed ({exc}), using fallback voices")
        return FALLBACK_VOICES


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def synthesize_to_file(
    settings: Settings,
    text: str,
    tts_provider: str | None = None,
    voice_id: str | None = None,
    el_stability: float | None = None,
    el_similarity: float | None = None,
    edge_voice: str | None = None,
    edge_rate: str | None = None,
    edge_pitch: str | None = None,
    speaker_wav: str | None = None,
) -> Path:
    """Generate narration audio file. Supports pyttsx3, ElevenLabs, Edge-TTS, and XTTS."""
    if not text.strip():
        raise ValueError("TTS text is empty")

    provider = (tts_provider or settings.tts_provider).strip().lower()

    tmpdir = Path(tempfile.mkdtemp(prefix="videobuild_tts_"))

    if provider == "elevenlabs":
        api_key = settings.elevenlabs_api_key
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not set in .env")
        vid = voice_id or settings.elevenlabs_voice_id
        out_path = tmpdir / "voice.mp3"
        stab = el_stability if el_stability is not None else settings.elevenlabs_stability
        sim = el_similarity if el_similarity is not None else settings.elevenlabs_similarity_boost
        try:
            _synthesize_elevenlabs(
                text, out_path, api_key, vid, settings.elevenlabs_model_id,
                stability=stab,
                similarity_boost=sim,
            )
        except Exception as exc:
            print(f"  ElevenLabs failed ({exc}), falling back to pyttsx3")
            out_path = tmpdir / "voice.wav"
            _synthesize_pyttsx3(settings, text, out_path)

    elif provider == "edge_tts":
        from .local_tts import synthesize_edge
        out_path = tmpdir / "voice.mp3"
        try:
            synthesize_edge(
                text, out_path,
                voice=edge_voice or "en-US-GuyNeural",
                rate=edge_rate or "+0%",
                pitch=edge_pitch or "+0Hz",
            )
        except Exception as exc:
            print(f"  Edge-TTS failed ({exc}), falling back to pyttsx3")
            out_path = tmpdir / "voice.wav"
            _synthesize_pyttsx3(settings, text, out_path)

    elif provider == "xtts":
        from .local_tts import synthesize_xtts
        out_path = tmpdir / "voice.wav"
        if not speaker_wav:
            raise ValueError("XTTS requires a voice sample (speaker_wav).")
        try:
            synthesize_xtts(
                text, out_path,
                speaker_wav=speaker_wav,
                language=settings.local_tts_language,
                model_name=settings.local_tts_model,
                device=settings.local_tts_device,
            )
        except Exception as exc:
            print(f"  XTTS failed ({exc}), falling back to pyttsx3")
            out_path = tmpdir / "voice.wav"
            _synthesize_pyttsx3(settings, text, out_path)

    else:
        out_path = tmpdir / "voice.wav"
        _synthesize_pyttsx3(settings, text, out_path)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("TTS audio file was not created")

    return out_path
