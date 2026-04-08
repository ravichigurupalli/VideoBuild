from __future__ import annotations

import requests
from huggingface_hub import InferenceClient

HF_CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
GEMINI_MODEL = "gemini-2.0-flash"
OLLAMA_DEFAULT_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2"

PROVIDERS = ("gemini", "huggingface", "ollama")

# Approximate words per second for narration pacing
_WORDS_PER_SECOND = 2.5

# Duration presets in seconds
DURATION_OPTIONS = {
    "30s": 30,
    "60s": 60,
    "90s": 90,
    "2min": 120,
    "3min": 180,
}


def _build_messages(topic: str, duration_label: str, video_format: str) -> list[dict]:
    seconds = DURATION_OPTIONS.get(duration_label, 60)
    word_count = int(seconds * _WORDS_PER_SECOND)
    format_name = "YouTube Short (vertical 9:16)" if video_format == "short" else "YouTube Video (horizontal 16:9)"

    system_msg = (
        "You are a professional YouTube script writer. "
        "You write narration scripts that are engaging, clear, and optimized for text-to-speech. "
        "You output ONLY the narration text with no stage directions, scene labels, markdown, or formatting."
    )
    user_msg = (
        f"Write a compelling narration script for a {format_name} about:\n"
        f'"{topic}"\n\n'
        f"Requirements:\n"
        f"- Target length: approximately {word_count} words ({seconds} seconds when spoken)\n"
        f"- Write ONLY the narration text\n"
        f"- Use short, punchy sentences that work well with text-to-speech\n"
        f"- Each sentence should describe a visual scene that can be illustrated\n"
        f"- Start with a hook to grab attention\n"
        f"- End with a call to action or thought-provoking conclusion\n"
        f"- Output ONLY the script text, nothing else"
    )
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


# ---------------------------------------------------------------------------
# Provider: Google Gemini (REST API)
# ---------------------------------------------------------------------------
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

def _generate_gemini(messages: list[dict], max_tokens: int, api_key: str) -> str:
    url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": messages[0]["content"]}]},
        "contents": [{"parts": [{"text": messages[1]["content"]}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
            "topP": 0.9,
        },
    }
    resp = requests.post(url, json=payload, timeout=120)
    if resp.status_code == 429:
        raise RuntimeError(
            "Gemini free-tier quota exhausted. Either wait for quota reset, "
            "enable billing in Google AI Studio, or switch to another provider."
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text[:300]}")
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Unexpected Gemini response: {resp.text[:300]}")
    if not text:
        raise RuntimeError("Gemini returned an empty script. Try rephrasing your topic.")
    return text


# ---------------------------------------------------------------------------
# Provider: HuggingFace Inference
# ---------------------------------------------------------------------------
def _generate_huggingface(messages: list[dict], max_tokens: int, hf_token: str) -> str:
    client = InferenceClient(token=hf_token)
    result = client.chat_completion(
        model=HF_CHAT_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.7,
        top_p=0.9,
    )
    try:
        text = result.choices[0].message.content.strip()
    except (AttributeError, IndexError):
        raise RuntimeError(f"Unexpected HF response: {str(result)[:300]}")
    if not text:
        raise RuntimeError("HuggingFace returned an empty script. Try rephrasing your topic.")
    return text


# ---------------------------------------------------------------------------
# Provider: Ollama (local LLM)
# ---------------------------------------------------------------------------
def _generate_ollama(messages: list[dict], max_tokens: int) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.7, "top_p": 0.9},
    }
    try:
        resp = requests.post(f"{OLLAMA_DEFAULT_URL}/api/chat", json=payload, timeout=180)
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_DEFAULT_URL}. "
            "Make sure Ollama is running (ollama serve) and the model is pulled (ollama pull llama3.2)."
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama error ({resp.status_code}): {resp.text[:300]}")
    try:
        text = resp.json()["message"]["content"].strip()
    except (KeyError, TypeError):
        raise RuntimeError(f"Unexpected Ollama response: {resp.text[:300]}")
    if not text:
        raise RuntimeError("Ollama returned an empty script. Try rephrasing your topic.")
    return text


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_script(
    topic: str,
    provider: str = "gemini",
    hf_token: str | None = None,
    gemini_api_key: str | None = None,
    duration_label: str = "60s",
    video_format: str = "video",
) -> str:
    messages = _build_messages(topic, duration_label, video_format)
    seconds = DURATION_OPTIONS.get(duration_label, 60)
    max_tokens = int(seconds * _WORDS_PER_SECOND * 2.0)

    print(f"Generating script [{provider}]: topic='{topic[:60]}...' duration={duration_label} format={video_format}")

    if provider == "gemini":
        if not gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        return _generate_gemini(messages, max_tokens, gemini_api_key)

    elif provider == "huggingface":
        if not hf_token:
            raise RuntimeError("HF_API_TOKEN not set in .env")
        return _generate_huggingface(messages, max_tokens, hf_token)

    elif provider == "ollama":
        return _generate_ollama(messages, max_tokens)

    else:
        raise RuntimeError(f"Unknown provider '{provider}'. Use one of: {', '.join(PROVIDERS)}")
