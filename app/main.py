from __future__ import annotations

import sys
from pathlib import Path

from .config import load_settings
from .video_builder import build_slideshow, default_title
from .youtube_client import upload_video


def main() -> int:
    settings = load_settings()
    print(f"Project root: {settings.project_root}")

    slides_dir = settings.slides_dir
    image_paths = sorted(slides_dir.glob("*.*"))
    print(f"Found {len(image_paths)} slide(s) in {slides_dir}")

    try:
        narration = settings.video_description if settings.enable_tts else None
        video_path = build_slideshow(settings, image_paths, narration=narration)
        print(f"Rendered video to {video_path}")

        title = default_title(settings)
        description = settings.video_description
        #upload_video(settings, video_path, title, description)

        if not settings.keep_output and Path(video_path).exists():
            Path(video_path).unlink()
            print("Deleted rendered file (set KEEP_OUTPUT=true to retain).")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
