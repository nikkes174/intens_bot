from __future__ import annotations

from datetime import datetime, timezone, timedelta
from sqlalchemy import func, select

from app.admin.keyboards import main_keyboard
from app.database import session_scope
from app.models import Detection, DetectionMention, Mention, Source


async def render_stats():
    async with session_scope() as db:
        total_detections = await db.scalar(select(func.count()).select_from(Detection))
        total_sources = await db.scalar(select(func.count()).select_from(Source))
        total_mentions = await db.scalar(select(func.count()).select_from(Mention))
        active_mentions = await db.scalar(
            select(func.count()).select_from(Mention).where(Mention.enabled.is_(True))
        )
        now = datetime.now(timezone.utc)
        since_7 = now - timedelta(days=7)
        since_30 = now - timedelta(days=30)
        last_7 = await db.scalar(
            select(func.count()).select_from(Detection).where(Detection.created_at >= since_7)
        )
        last_30 = await db.scalar(
            select(func.count()).select_from(Detection).where(Detection.created_at >= since_30)
        )

        top_result = await db.execute(
            select(Mention.keyword, func.count(DetectionMention.id).label("cnt"))
            .join(DetectionMention, DetectionMention.mention_id == Mention.id)
            .group_by(Mention.id)
            .order_by(func.count(DetectionMention.id).desc())
            .limit(5)
        )
        top_mentions = top_result.all()

    text = (
        "📊 Статистика\n\n"
        f"Всего обнаружений: {total_detections or 0}\n"
        f"За 7 дней: {last_7 or 0}\n"
        f"За 30 дней: {last_30 or 0}\n"
        f"Источников: {total_sources or 0}\n"
        f"Упоминаний: {total_mentions or 0} (активных: {active_mentions or 0})\n\n"
        "Топ упоминаний:\n"
    )
    if top_mentions:
        for keyword, count in top_mentions:
            text += f"- {keyword}: {count}\n"
    else:
        text += "нет данных\n"
    return text, main_keyboard()
