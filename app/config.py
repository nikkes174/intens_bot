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
    web_monitor_enabled: bool = os.getenv("WEB_MONITOR_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    web_monitor_interval: int = max(
        30,
        int(os.getenv("WEB_MONITOR_INTERVAL", "30") or 30),
    )
    web_monitor_timeout: int = int(os.getenv("WEB_MONITOR_TIMEOUT", "15") or 15)
    web_monitor_max_concurrency: int = max(
        1,
        int(os.getenv("WEB_MONITOR_MAX_CONCURRENCY", "3") or 3),
    )
    web_gdelt_min_interval: int = max(
        30,
        int(os.getenv("WEB_GDELT_MIN_INTERVAL", "60") or 60),
    )
    web_monitor_freshness_hours: int = max(
        1,
        int(os.getenv("WEB_MONITOR_FRESHNESS_HOURS", "168") or 168),
    )
    web_rss_urls: str = os.getenv("WEB_RSS_URLS", "")
    web_webhook_enabled: bool = os.getenv("WEB_WEBHOOK_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    web_webhook_host: str = os.getenv("WEB_WEBHOOK_HOST", "127.0.0.1")
    web_webhook_port: int = int(os.getenv("WEB_WEBHOOK_PORT", "8080") or 8080)
    web_webhook_secrets: str = os.getenv("WEB_WEBHOOK_SECRETS", "")
    web_webhook_max_body: int = max(
        1024,
        int(os.getenv("WEB_WEBHOOK_MAX_BODY", "1048576") or 1048576),
    )


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def is_admin(user_id: int) -> bool:
    settings = get_settings()
    return settings.admin_id != 0 and user_id == settings.admin_id
