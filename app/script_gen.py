from __future__ import annotations

from huggingface_hub import InferenceClient

HF_CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"

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


def generate_script(
    topic: str,
    hf_token: str,
    duration_label: str = "60s",
    video_format: str = "video",
) -> str:
    messages = _build_messages(topic, duration_label, video_format)
    seconds = DURATION_OPTIONS.get(duration_label, 60)
    max_tokens = int(seconds * _WORDS_PER_SECOND * 2.0)

    print(f"Generating script: topic='{topic[:60]}...' duration={duration_label} format={video_format}")

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
        raise RuntimeError(f"Unexpected API response: {str(result)[:300]}")

    if not text:
        raise RuntimeError("AI returned an empty script. Try rephrasing your topic.")

    return text
