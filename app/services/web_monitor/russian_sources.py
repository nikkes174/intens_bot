from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select

from app.database import session_scope
from app.models import Setting


@dataclass(frozen=True)
class MediaSource:
    name: str
    domain: str
    rss_url: str | None
    enabled: bool = True
    status_note: str = ""


# URLs are kept only where an official endpoint is known.  Sources without a
# confirmed usable official feed remain available through Google News/GDELT.
RUSSIAN_MEDIA = [
    MediaSource(
        "РБК",
        "rbc.ru",
        None,
        status_note="Официальный RSS-адрес не отвечает; используется через агрегаторы.",
    ),
    MediaSource("ТАСС", "tass.ru", "https://tass.ru/rss/v2.xml"),
    MediaSource("РИА Новости", "ria.ru", "https://ria.ru/export/rss2/archive/index.xml"),
    MediaSource(
        "Интерфакс",
        "interfax.ru",
        None,
        status_note="RSS не включён: правила Интерфакса ограничивают использование RSS.",
    ),
    MediaSource("Коммерсантъ", "kommersant.ru", "https://www.kommersant.ru/RSS/news.xml"),
    MediaSource("Ведомости", "vedomosti.ru", "https://www.vedomosti.ru/rss/news"),
    MediaSource(
        "Известия",
        "iz.ru",
        None,
        status_note="RSS-адрес отвечает отказом 403; источник остаётся доступен через агрегаторы.",
    ),
    MediaSource("Газета.Ru", "gazeta.ru", "https://www.gazeta.ru/export/rss/first.xml"),
    MediaSource("Lenta.ru", "lenta.ru", "https://lenta.ru/rss/"),
    MediaSource("Российская газета", "rg.ru", "https://rg.ru/xml/index.xml"),
    MediaSource(
        "Forbes Russia",
        "forbes.ru",
        None,
        status_note="Указанный RSS-адрес возвращает HTML вместо RSS/Atom XML.",
    ),
    MediaSource(
        "Фонтанка",
        "fontanka.ru",
        None,
        status_note="Текущий официальный RSS-адрес не удалось достоверно подтвердить.",
    ),
]

_ENABLED_PREFIX = "web_russian_media_enabled:"
_STATUS_PREFIX = "web_russian_media_status:"


def _key(prefix: str, domain: str) -> str:
    return f"{prefix}{domain.casefold().strip().strip('/')}"


def _source_by_domain(domain: str) -> MediaSource | None:
    normalized = domain.casefold().removeprefix("www.").strip().strip("/")
    return next((source for source in RUSSIAN_MEDIA if source.domain == normalized), None)


def catalog_source_domains() -> set[str]:
    return {source.domain for source in RUSSIAN_MEDIA}


def source_for_url(url: str) -> MediaSource | None:
    hostname = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return _source_by_domain(hostname)


def merge_rss_feeds(
    user_feeds: list[tuple[str, str]],
    sources: list[MediaSource] | None = None,
) -> list[tuple[str, str]]:
    """Combine built-in and user feeds without changing WEB_RSS_URLS semantics."""

    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source_name, url in [
        *user_feeds,
        *((source.name, source.rss_url) for source in (sources or RUSSIAN_MEDIA) if source.rss_url),
    ]:
        normalized_url = str(url).strip().rstrip("/").casefold()
        if not normalized_url or normalized_url in seen:
            continue
        seen.add(normalized_url)
        merged.append((str(source_name).strip() or "RSS", str(url).strip()))
    return merged


async def get_source_enabled(source: MediaSource) -> bool:
    if not source.enabled:
        return False
    async with session_scope() as db:
        row = await db.get(Setting, _key(_ENABLED_PREFIX, source.domain))
        if row is None:
            return True
        return row.value.casefold() in {"1", "true", "yes", "on"}


async def set_source_enabled(domain: str, enabled: bool) -> bool:
    source = _source_by_domain(domain)
    if source is None:
        raise ValueError("Unknown Russian media source")
    key = _key(_ENABLED_PREFIX, source.domain)
    async with session_scope() as db:
        row = await db.get(Setting, key)
        value = "true" if enabled else "false"
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await db.commit()
    return enabled


async def get_enabled_sources() -> list[MediaSource]:
    enabled: list[MediaSource] = []
    for source in RUSSIAN_MEDIA:
        if source.rss_url and await get_source_enabled(source):
            enabled.append(source)
    return enabled


async def save_source_status(
    domain: str,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    source = _source_by_domain(domain)
    if source is None:
        return
    payload = json.dumps(
        {
            "status": "ok" if ok else "error",
            "error": error or "",
            "checked_at": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )
    key = _key(_STATUS_PREFIX, source.domain)
    async with session_scope() as db:
        row = await db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=payload))
        else:
            row.value = payload
            row.updated_at = datetime.now(UTC)
        await db.commit()


async def get_source_statuses() -> dict[str, dict[str, str]]:
    async with session_scope() as db:
        result = await db.execute(select(Setting).where(Setting.key.like(f"{_STATUS_PREFIX}%")))
        rows = list(result.scalars().all())
    statuses: dict[str, dict[str, str]] = {}
    for row in rows:
        domain = row.key.removeprefix(_STATUS_PREFIX)
        try:
            value = json.loads(row.value)
        except (TypeError, ValueError):
            value = {"status": "error", "error": row.value}
        statuses[domain] = {
            "status": str(value.get("status") or "unknown"),
            "error": str(value.get("error") or ""),
            "checked_at": str(value.get("checked_at") or ""),
        }
    return statuses


async def get_source_overview() -> list[dict[str, object]]:
    statuses = await get_source_statuses()
    overview: list[dict[str, object]] = []
    for source in RUSSIAN_MEDIA:
        enabled = await get_source_enabled(source)
        status = statuses.get(source.domain, {})
        overview.append(
            {
                "source": source,
                "enabled": enabled,
                "status": status.get("status")
                or ("unavailable" if source.rss_url is None else "unknown"),
                "error": status.get("error") or source.status_note,
                "checked_at": status.get("checked_at", ""),
            }
        )
    return overview
