from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def parse_admin_ids(value: str) -> tuple[int, ...]:
    result = []
    for item in value.split(","):
        item = item.strip()
        if item.isdigit() and int(item) > 0:
            result.append(int(item))
    return tuple(dict.fromkeys(result))


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: tuple[int, ...] = parse_admin_ids(os.getenv("ADMIN_ID", ""))
    api_id: int = _env_int("API_ID", 0)
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
    queue_size: int = _env_int("QUEUE_SIZE", 100, 1)
    max_attempts: int = _env_int("MAX_ATTEMPTS", 5, 1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    web_monitor_enabled: bool = _env_bool("WEB_MONITOR_ENABLED", True)
    web_monitor_interval: int = _env_int("WEB_MONITOR_INTERVAL", 30, 30)
    web_monitor_timeout: int = _env_int("WEB_MONITOR_TIMEOUT", 15, 1)
    web_monitor_max_concurrency: int = _env_int("WEB_MONITOR_MAX_CONCURRENCY", 3, 1)
    web_gdelt_min_interval: int = _env_int("WEB_GDELT_MIN_INTERVAL", 60, 30)
    web_monitor_freshness_hours: int = _env_int("WEB_MONITOR_FRESHNESS_HOURS", 168, 1)
    web_article_extraction_enabled: bool = _env_bool(
        "WEB_ARTICLE_EXTRACTION_ENABLED", True
    )
    web_article_max_concurrency: int = _env_int("WEB_ARTICLE_MAX_CONCURRENCY", 3, 1)
    web_article_timeout: int = _env_int("WEB_ARTICLE_TIMEOUT", 10, 1)
    web_russian_media_enabled: bool = _env_bool("WEB_RUSSIAN_MEDIA_ENABLED", True)
    web_russian_media_interval: int = _env_int("WEB_RUSSIAN_MEDIA_INTERVAL", 300, 60)
    web_rss_urls: str = os.getenv("WEB_RSS_URLS", "")
    web_webhook_enabled: bool = _env_bool("WEB_WEBHOOK_ENABLED", False)
    web_webhook_host: str = os.getenv("WEB_WEBHOOK_HOST", "127.0.0.1")
    web_webhook_port: int = _env_int("WEB_WEBHOOK_PORT", 8080, 1)
    web_webhook_secrets: str = os.getenv("WEB_WEBHOOK_SECRETS", "")
    web_webhook_max_body: int = _env_int("WEB_WEBHOOK_MAX_BODY", 1048576, 1024)
    web_source_autodiscovery_enabled: bool = _env_bool(
        "WEB_SOURCE_AUTODISCOVERY_ENABLED", True
    )
    web_rss_discovery_enabled: bool = _env_bool("WEB_RSS_DISCOVERY_ENABLED", True)
    web_sitemap_enabled: bool = _env_bool("WEB_SITEMAP_ENABLED", True)
    web_public_news_pages_enabled: bool = _env_bool(
        "WEB_PUBLIC_NEWS_PAGES_ENABLED", False
    )
    web_source_check_concurrency: int = _env_int("WEB_SOURCE_CHECK_CONCURRENCY", 5, 1)
    web_source_poll_concurrency: int = _env_int("WEB_SOURCE_POLL_CONCURRENCY", 10, 1)
    web_source_discovery_daily_limit: int = _env_int(
        "WEB_SOURCE_DISCOVERY_DAILY_LIMIT", 100, 1
    )
    web_source_request_timeout: int = _env_int("WEB_SOURCE_REQUEST_TIMEOUT", 10, 1)
    web_source_max_candidates_per_run: int = _env_int(
        "WEB_SOURCE_MAX_CANDIDATES_PER_RUN", 5, 1
    )
    web_source_max_sitemap_entries: int = _env_int(
        "WEB_SOURCE_MAX_SITEMAP_ENTRIES", 10000, 1
    )
    web_source_max_sitemaps_per_domain: int = _env_int(
        "WEB_SOURCE_MAX_SITEMAPS_PER_DOMAIN", 20, 1
    )
    web_source_max_sitemap_depth: int = _env_int(
        "WEB_SOURCE_MAX_SITEMAP_DEPTH", 3, 1
    )
    web_source_max_sitemap_documents: int = _env_int(
        "WEB_SOURCE_MAX_SITEMAP_DOCUMENTS", 20, 1
    )
    web_source_max_sitemap_bytes: int = _env_int(
        "WEB_SOURCE_MAX_SITEMAP_BYTES", 52428800, 1024
    )

    @property
    def admin_id(self) -> int:
        """Return the first administrator for backwards compatibility."""

        return self.admin_ids[0] if self.admin_ids else 0

    @admin_id.setter
    def admin_id(self, value: int) -> None:
        self.admin_ids = (value,) if value > 0 else ()


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def is_admin(user_id: int) -> bool:
    settings = get_settings()
    return user_id in settings.admin_ids
