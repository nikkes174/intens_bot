from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_id: int = int(os.getenv("ADMIN_ID", "0") or 0)
    api_id: int = int(os.getenv("API_ID", "0") or 0)
    api_hash: str = os.getenv("API_HASH", "")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./data/monitor.db",
    )
    session_name: str = os.getenv("SESSION_NAME", "monitor")
    session_path: str = os.getenv("SESSION_PATH", "./data/monitor.session")
    monitoring_enabled: bool = os.getenv("MONITORING_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    source_mode: str = os.getenv("SOURCE_MODE", "all")
    timezone: str = os.getenv("TIMEZONE", "Asia/Bangkok")
    queue_size: int = int(os.getenv("QUEUE_SIZE", "100") or 100)
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "5") or 5)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def is_admin(user_id: int) -> bool:
    settings = get_settings()
    return settings.admin_id != 0 and user_id == settings.admin_id
