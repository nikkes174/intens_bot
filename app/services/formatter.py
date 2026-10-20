from __future__ import annotations
from typing import Optional

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.userbot.sources import public_post_url  # noqa: F401


MAX_MESSAGE_LENGTH = 4096


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

    audience_text = (
        f"{audience:,}".replace(",", " ") if audience is not None else "неизвестна"
    )

    found = ", ".join(escape(word) for word in (matches or []))
    safe_title = escape(title or "")
    safe_text = escape(text or "")

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
    if len(safe_text) > available:
        if available <= 1:
            safe_text = ""
        else:
            safe_text = safe_text[: available - 1].rstrip() + "…"

    return head + safe_text + tail
