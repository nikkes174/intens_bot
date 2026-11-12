from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.text import clean_article_text

MAX_MESSAGE_LENGTH = 4096


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _truncate_escaped(value: str, max_length: int) -> str:
    if max_length <= 0:
        return ""
    escaped = escape(value)
    if len(escaped) <= max_length:
        return escaped
    low, high = 0, len(value)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = escape(value[:middle]) + "…"
        if len(candidate) <= max_length:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best or "…"[:max_length]


def _safe_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def _article_snippet(article, needles: Iterable[str]) -> str | None:
    full_text = clean_article_text(str(getattr(article, "full_text", "") or ""))
    if not full_text:
        return None
    folded = full_text.casefold()
    position = -1
    matched_length = 0
    for needle in needles:
        value = " ".join(str(needle or "").split()).strip().casefold()
        if not value:
            continue
        position = folded.find(value)
        if position >= 0:
            matched_length = len(value)
            break
    if position < 0:
        return full_text[:420]
    start = max(0, position - 180)
    end = min(len(full_text), position + matched_length + 240)
    prefix = "…" if start else ""
    suffix = "…" if end < len(full_text) else ""
    return f"{prefix}{full_text[start:end].strip()}{suffix}"


def format_web_notification(
    article: WebArticle,
    watch_queries: Iterable[str] | str,
    *,
    detected_at: datetime | None = None,
    timezone_name: str = "UTC",
    matched_texts: Iterable[str] | None = None,
    watch_types: Iterable[str] | None = None,
) -> str:
    """Render a Telegram-safe web notification, capped at 4096 characters."""

    if isinstance(watch_queries, str):
        watch_queries = [watch_queries]
    queries = [str(item) for item in watch_queries if item]
    type_icons = {"person": "👤", "organization": "🏢", "keyword": "🔎"}
    types = list(watch_types or ())
    watch_values = [
        f"{type_icons.get(types[index], '🔎')} {query}"
        if index < len(types)
        else query
        for index, query in enumerate(queries)
    ]
    watches = ", ".join(dict.fromkeys(watch_values)) or "—"
    published = _as_utc(article.published_at)
    detected = _as_utc(detected_at) or datetime.now(UTC)
    zone = _safe_zone(timezone_name)
    published_text = published.astimezone(zone).strftime("%d.%m.%Y, %H:%M") if published else "неизвестно"
    detected_text = detected.astimezone(zone).strftime("%H:%M")

    safe_values = {
        "watches": _truncate_escaped(watches, 600),
        "source": _truncate_escaped(
            clean_article_text(article.source_name) or "Неизвестный источник", 255
        ),
        "title": _truncate_escaped(
            clean_article_text(article.title) or "Без заголовка", 1200
        ),
    }
    snippet = _article_snippet(article, [*(matched_texts or ()), *watches.split(", ")])
    body_label = "💬 <b>Фрагмент:</b>\n" if snippet else "📝 <b>Описание:</b>\n"
    prefix = (
        "🌐 <b>НОВОЕ УПОМИНАНИЕ В ИНТЕРНЕТЕ</b>\n\n"
        f"🔎 Цель мониторинга: {safe_values['watches']}\n\n"
        f"📰 Источник: {safe_values['source']}\n"
        f"📅 Опубликовано: {published_text}\n\n"
        "📌 <b>Заголовок:</b>\n"
        f"{safe_values['title']}\n\n"
        + body_label
    )
    suffix = f"\n\n🔎 Обнаружено: {detected_text}"
    available = MAX_MESSAGE_LENGTH - len(prefix) - len(suffix)
    description = _truncate_escaped(
        snippet
        or clean_article_text(article.description)
        or "Описание отсутствует",
        max(0, available),
    )
    result = prefix + description + suffix
    # The calculation above is exact for normal Unicode strings; retain a
    # final guard for unusual Python/Telegram formatting edge cases.
    return result


def web_article_keyboard(url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть публикацию", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Alias used by integrations that call all formatters uniformly.
format_notification = format_web_notification
