from __future__ import annotations

from pathlib import Path
from telethon import TelegramClient

from app.config import get_settings


def create_telethon_client():
    settings = get_settings()
    Path(settings.session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        settings.session_path,
        settings.api_id,
        settings.api_hash,
    )


def create_client(*args, **kwargs):
    return create_telethon_client(*args, **kwargs)
