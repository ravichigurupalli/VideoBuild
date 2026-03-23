from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import Settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_credentials(settings: Settings) -> Credentials:
    creds: Optional[Credentials] = None
    if settings.token_file.exists():
        creds = Credentials.from_authorized_user_file(str(settings.token_file), SCOPES)

    if not creds or not creds.valid:
        if not settings.client_secret_file.exists() or settings.client_secret_file.stat().st_size == 0:
            raise FileNotFoundError(
                f"client_secret.json is missing or empty at {settings.client_secret_file}"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(settings.client_secret_file), SCOPES
        )
        creds = flow.run_local_server(port=0)
        settings.token_file.write_text(creds.to_json())
    return creds


def get_youtube_service(settings: Settings):
    creds = _get_credentials(settings)
    return build("youtube", "v3", credentials=creds)


def set_thumbnail(settings: Settings, video_id: str, thumbnail_path: Path) -> None:
    youtube = get_youtube_service(settings)
    media = MediaFileUpload(str(thumbnail_path))
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    print(f"Thumbnail applied: {thumbnail_path}")


def upload_video(
    settings: Settings,
    video_path: Path,
    title: str,
    description: str,
    thumbnail_path: Path | None = None,
) -> str:
    youtube = get_youtube_service(settings)
    body: Dict[str, object] = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": str(settings.category_id),
        },
        "status": {"privacyStatus": settings.video_privacy},
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"Uploaded: https://youtube.com/watch?v={video_id}")

    if thumbnail_path and thumbnail_path.exists():
        set_thumbnail(settings, video_id, thumbnail_path)

    return video_id
