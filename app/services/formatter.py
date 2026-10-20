from __future__ import annotations
from typing import Optional

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.userbot.sources import public_post_url  # noqa: F401

MAX_MESSAGE_LENGTH = 4096


def _truncate_raw(text: str, available: int) -> str:
    if available <= 0:
        return ""
    if len(escape(text)) <= available:
        return text
    lo, hi = 0, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid]
        if mid < len(text):
            candidate += "…"
        if len(escape(candidate)) <= available:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    result = text[:best]
    if best < len(text):
        result += "…"
    while result and len(escape(result)) > available:
        result = result[:-1]
        if result and not result.endswith("…") and best < len(text):
            result += "…"
    return result


def format_notification(
    title: str,
    published_at: datetime,
    audience: Optional[int],
    matches: list[str],
    text: str,
    url: Optional[str],
    timezone_name: str = "Asia/Bangkok",
) -> str:
    local_date = published_at.astimezone(ZoneInfo(timezone_name))
    date_text = local_date.strftime("%d.%m.%Y, %H:%M")

    audience_text = f"{audience:,}".replace(",", " ") if audience is not None else "неизвестна"

    found = ", ".join(escape(word) for word in (matches or []))
    safe_title = escape(title or "")

    head = (
        f"🌐 <b>Telegram — «{safe_title}»</b>\n"
        f"📅 {date_text}\n"
        f"👥 Аудитория: {audience_text}\n\n"
        f"🔎 <b>Найдено: {found}</b>\n\n"
    )
    tail = f"\n\n🔗 {escape(url)}" if url else ""

    available = MAX_MESSAGE_LENGTH - len(head) - len(tail)
    if available < 0:
        available = 0
    truncated_raw = _truncate_raw(text or "", available)
    safe_text = escape(truncated_raw)

    # Final safety trim, should not normally trigger.
    while len(head) + len(safe_text) + len(tail) > MAX_MESSAGE_LENGTH and safe_text:
        safe_text = safe_text[:-1]

    return head + safe_text + tail
