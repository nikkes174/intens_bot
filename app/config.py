from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _parse_admin_ids(value: str) -> List[int]:
    result = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                continue
    return result


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: List[int] = field(default_factory=list)
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

    def __post_init__(self):
        if not self.admin_ids:
            self.admin_ids = _parse_admin_ids(os.getenv("ADMIN_ID", ""))


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def is_admin(user_id: int) -> bool:
    return user_id in get_settings().admin_ids
