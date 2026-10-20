from __future__ import annotations

from datetime import datetime, timezone, timedelta
from sqlalchemy import func, select

from app.admin.keyboards import main_keyboard
from app.database import session_scope
from app.models import Detection, Mention, Source


async def render_stats():
    async with session_scope() as db:
        total_detections = await db.scalar(select(func.count()).select_from(Detection))
        total_sources = await db.scalar(select(func.count()).select_from(Source))
        total_mentions = await db.scalar(select(func.count()).select_from(Mention))
        active_mentions = await db.scalar(
            select(func.count()).select_from(Mention).where(Mention.enabled.is_(True))
        )
        since = datetime.now(timezone.utc) - timedelta(days=1)
        today = await db.scalar(
            select(func.count())
            .select_from(Detection)
            .where(Detection.created_at >= since)
        )

    text = (
        "📊 Статистика\n\n"
        f"Всего обнаружений: {total_detections or 0}\n"
        f"За последние 24 часа: {today or 0}\n"
        f"Источников: {total_sources or 0}\n"
        f"Упоминаний: {total_mentions or 0} (активных: {active_mentions or 0})"
    )
    return text, main_keyboard()
