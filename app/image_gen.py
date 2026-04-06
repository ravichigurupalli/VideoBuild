from __future__ import annotations

import io
from pathlib import Path

import requests
from PIL import Image

HF_API_URL = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"

# Aspect-ratio-aware sizes supported well by SDXL
_FORMAT_SIZES = {
    "video": (1344, 768),   # ~16:9
    "short": (768, 1344),   # ~9:16
}


def generate_image(
    prompt: str,
    hf_token: str,
    video_format: str = "video",
    output_path: Path | None = None,
) -> Path:
    width, height = _FORMAT_SIZES.get(video_format, _FORMAT_SIZES["video"])

    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "width": width,
            "height": height,
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        },
        "options": {"wait_for_model": True},
    }

    print(f"Generating image: format={video_format} size={width}x{height}")
    response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=120)

    if response.status_code != 200:
        error_detail = response.text[:500]
        raise RuntimeError(f"HuggingFace API error ({response.status_code}): {error_detail}")

    image = Image.open(io.BytesIO(response.content))

    if output_path is None:
        output_path = Path("generated_image.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(output_path), format="PNG")
    print(f"Image saved: {output_path} ({image.size[0]}x{image.size[1]})")
    return output_path
