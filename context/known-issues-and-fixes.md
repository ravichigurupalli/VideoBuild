# Known Issues and Fixes

---

## 1. torchcodec / FFmpeg DLL Error on Windows

### Symptom
```
RuntimeError: Could not load libtorchcodec. Likely causes:
1. FFmpeg is not properly installed in your environment
```
Or:
```
ImportError: From Pytorch 2.9, the torchcodec library is required...
```

### Root Cause
- PyTorch 2.9+ removed the old torchaudio backends and requires `torchcodec`
- `torchcodec` needs FFmpeg shared DLLs (`avcodec-*.dll`, etc.) at import time
- These DLLs are not available in a standard Windows Python venv

### Fix Applied
**Two-layer fix:**

**Layer 1** — Patch `TTS/__init__.py` (installed package) to comment out the torchcodec import check:
```python
# Patched: skip torchcodec check (Windows — no FFmpeg shared DLLs)
# if is_torch_greater_or_equal("2.9"):
#     if not is_torchcodec_available():
#         raise ImportError(TORCHCODEC_IMPORT_ERROR)
```
File: `.venv\Lib\site-packages\TTS\__init__.py` (around line 60)

**Layer 2** — Replace `torchaudio.load` with a `soundfile`-based implementation in `app/local_tts._patch_transformers()`:
```python
def _sf_load(filepath, frame_offset=0, num_frames=-1, ...):
    data, sample_rate = sf.read(str(filepath), dtype="float32", ...)
    waveform = torch.from_numpy(data)
    if channels_first:
        waveform = waveform.T
    return waveform, sample_rate

torchaudio.load = _sf_load
```
This replaces torchaudio's built-in loader that uses torchcodec internally.

### Prevention
If re-installing packages resets `TTS/__init__.py`, re-apply the patch manually.

---

## 2. Coqui TOS Auto-Accept

### Symptom
```
EOF when reading a line
```
The Coqui TTS model downloader prompts for interactive TOS acceptance, but there's no terminal available in a Flask server context.

### Fix Applied
`app/local_tts._accept_coqui_tos()` sets the environment variable before model download:
```python
os.environ["COQUI_TOS_AGREED"] = "1"
```
This bypasses the interactive prompt entirely.

---

## 3. SSL Certificate Bypass for Model Download

### Symptom
```
SSLError: [SSL: CERTIFICATE_VERIFY_FAILED]
```
Occurs when downloading the XTTS model from HuggingFace Hub through a corporate proxy that performs SSL inspection.

### Fix Applied
`app/local_tts._accept_coqui_tos()`:
```python
os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
ssl._create_default_https_context = ssl._create_unverified_context
```

And in `_get_tts()`, requests is monkey-patched before the download:
```python
def _patched_send(self, request, **kwargs):
    kwargs["verify"] = False
    return _orig_get(self, request, **kwargs)
_req.Session.send = _patched_send
```
Original send is restored after the model loads.

---

## 4. .webm Voice Samples Not Accepted by XTTS

### Symptom
XTTS rejects audio files recorded by the browser's `MediaRecorder` API because browsers record in `.webm` format, and XTTS requires `.wav` (22050 Hz mono).

### Fix Applied
`app/web.py /local-tts/upload-voice` automatically converts non-WAV files after upload:
```python
if ext != ".wav":
    wav_dest = voice_samples_dir / (voice_name + ".wav")
    if _convert_to_wav(src, wav_dest):
        src.unlink()   # delete original
```

`_convert_to_wav()` uses the ffmpeg bundled with `imageio-ffmpeg` (no system ffmpeg required):
```python
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
subprocess.run([ffmpeg_exe, "-y", "-i", str(src), "-ar", "22050", "-ac", "1", str(dst)])
```

---

## 5. ElevenLabs Corporate Proxy Interception

### Symptom
ElevenLabs API returns an HTML page (200 OK) instead of JSON/audio, causing:
```json
{ "error": "Corporate proxy/firewall is blocking requests to api.elevenlabs.io..." }
```

### Fix Applied
`app/tts._check_proxy_intercept()` detects HTML responses:
```python
if "text/html" in resp.headers.get("content-type", "").lower():
    raise RuntimeError("Corporate proxy/firewall is blocking requests...")
```

`_el_session()` creates a session with `verify=False` and proxy passthrough:
```python
s.verify = False
proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
if proxy:
    s.proxies = {"https": proxy, "http": proxy}
```

### Fallback
If ElevenLabs synthesis fails for any reason, `synthesize_to_file()` silently falls back to pyttsx3.

---

## 6. ElevenLabs Voices API Fallback

### Symptom
`GET /elevenlabs-voices` returns an error or empty list if the API is unreachable.

### Fix Applied
`app/tts.fetch_elevenlabs_voices()` catches all exceptions and returns a hardcoded fallback:
```python
FALLBACK_VOICES = [
    {"voice_id": "57WpXhyNwaU0uXgMrmDS", "name": "My Voice (cloned)", ...},
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", ...},
]
```

---

## 7. Pillow ANTIALIAS Compatibility

### Symptom
```
AttributeError: module 'PIL.Image' has no attribute 'ANTIALIAS'
```
Pillow 10+ removed `Image.ANTIALIAS`; MoviePy 1.0.3 still uses it internally.

### Fix Applied
Compatibility shim in both `video_builder.py` and `text_to_video.py`:
```python
if not hasattr(Image, "ANTIALIAS") and hasattr(Image, "Resampling"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS
```

---

## 8. transformers isin_mps_friendly Missing

### Symptom
```
AttributeError: module 'transformers.pytorch_utils' has no attribute 'isin_mps_friendly'
```
Occurs with newer `transformers` versions where this utility was removed/renamed.

### Fix Applied
`app/local_tts._patch_transformers()`:
```python
import transformers.pytorch_utils as pu
if not hasattr(pu, "isin_mps_friendly"):
    pu.isin_mps_friendly = torch.isin
```

---

## 9. Gemini API Quota Exhaustion

### Symptom
```
RuntimeError: Gemini free-tier quota exhausted.
```
HTTP 429 from Gemini API.

### Fix Applied
`app/script_gen._generate_gemini()` handles 429 explicitly with a clear error message directing the user to either wait, enable billing, or switch providers.

### Workaround
Switch script provider to `huggingface` or `ollama` in the UI or set `SCRIPT_PROVIDER=huggingface` in `.env`.

---

## 10. HuggingFace Inference API URL Change

### Symptom
HTTP 410 Gone from old HuggingFace API URL.

### Fix Applied
Updated API URLs in `image_gen.py` and `text_to_video.py` to the new router format:
```
# Old (deprecated):
https://api-inference.huggingface.co/models/{model}

# New:
https://router.huggingface.co/hf-inference/models/{model}
```

---

## 11. Build & Upload Using Default Voice (Not Selected Voice)

### Symptom
When "Build & Upload Video" was used, the video narration used the default pyttsx3 voice (Microsoft David) instead of the Edge-TTS or XTTS voice selected in the dropdown.

### Root Cause
The `tts_provider`, `edge_voice`, `speaker_wav` etc. form fields were not being sent with the build request.

### Fix Applied
`web.py /build` now reads all TTS params from the form and passes them to `build_slideshow()`:
```python
tts_provider = request.form.get("tts_provider") or settings.tts_provider
edge_voice = request.form.get("edge_voice") or None
speaker_wav_name = request.form.get("speaker_wav") or ""
...
video_builder.build_slideshow(..., tts_provider=tts_provider, edge_voice=edge_voice, ...)
```
The frontend also sends these params explicitly in the `FormData` for the build request.
