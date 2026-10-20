from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from sqlalchemy import select

from app.database import session_scope
from app.models import Exclusion, Source


AUDIENCE_TTL = timedelta(minutes=10)
_source_cache: dict[int, dict[str, Any]] = {}


def public_post_url(username: Optional[str], message_id: int) -> Optional[str]:
    if not username:
        return None
    return f"https://t.me/{username}/{message_id}"


def source_post_url(source: Optional[Source], message_id: int) -> Optional[str]:
    if source is None:
        return None
    if source.username:
        return public_post_url(source.username, message_id)
    if source.source_type in {"channel", "supergroup"}:
        telegram_id = str(source.telegram_id)
        if telegram_id.startswith("-100"):
            internal_id = telegram_id[4:]
            return f"https://t.me/c/{internal_id}/{message_id}"
    return None


async def ensure_source_from_event(event: Any) -> None:
    chat = getattr(event, "chat", None)
    source_id = getattr(event, "chat_id", None)
    if source_id is None:
        return
    title = getattr(chat, "title", None) or ""
    username = getattr(chat, "username", None)
    source_type = None
    if getattr(chat, "broadcast", False):
        source_type = "channel"
    elif getattr(chat, "megagroup", False):
        source_type = "supergroup"
    elif getattr(event, "is_group", False):
        source_type = "group"

    async with session_scope() as db:
        result = await db.execute(select(Source).where(Source.telegram_id == source_id))
        source = result.scalar_one_or_none()
        if source is None:
            source = Source(
                telegram_id=source_id,
                title=title,
                username=username,
                source_type=source_type,
                enabled=True,
            )
            db.add(source)
        else:
            if title:
                source.title = title
            if username is not None:
                source.username = username
            if source_type is not None:
                source.source_type = source_type
        await db.commit()


async def get_source(source_id: int) -> Optional[Source]:
    async with session_scope() as db:
        result = await db.execute(select(Source).where(Source.telegram_id == source_id))
        source = result.scalar_one_or_none()
        if source is not None:
            db.expunge(source)
        return source


async def get_source_title(source_id: int) -> str:
    source = await get_source(source_id)
    if source and source.title:
        return source.title
    return f"Chat {source_id}"


async def get_source_username(source_id: int) -> Optional[str]:
    source = await get_source(source_id)
    return source.username if source else None


async def get_source_type(source_id: int) -> Optional[str]:
    source = await get_source(source_id)
    return source.source_type if source else None


async def get_source_audience(client: Any, source_id: int) -> Optional[int]:
    cached = _source_cache.get(source_id)
    now = time.monotonic()
    if cached and now - cached["time"] < AUDIENCE_TTL.total_seconds():
        return cached["audience"]

    source = await get_source(source_id)
    audience = source.audience if source else None

    if audience is None and client is not None:
        try:
            entity = await client.get_entity(source_id)
            audience = getattr(entity, "participants_count", None)
            if audience is None:
                try:
                    participants = await client.get_participants(entity, limit=0)
                    audience = getattr(participants, "total", None)
                except Exception:
                    audience = None
        except Exception:
            audience = None

        if source is not None:
            async with session_scope() as db:
                db_source = await db.get(Source, source.id)
                if db_source is not None:
                    db_source.audience = audience
                    db_source.audience_updated_at = datetime.now(timezone.utc)
                    await db.commit()

    _source_cache[source_id] = {"time": now, "audience": audience}
    return audience


def invalidate_source_cache(source_id: Optional[int] = None) -> None:
    if source_id is None:
        _source_cache.clear()
    else:
        _source_cache.pop(source_id, None)


async def list_sources() -> list[Source]:
    async with session_scope() as db:
        result = await db.execute(select(Source).order_by(Source.id))
        sources = list(result.scalars().all())
        for source in sources:
            db.expunge(source)
        return sources


async def set_source_enabled(source_id: int, enabled: bool) -> None:
    async with session_scope() as db:
        result = await db.execute(select(Source).where(Source.telegram_id == source_id))
        source = result.scalar_one_or_none()
        if source is not None:
            source.enabled = enabled
            await db.commit()


async def set_source_excluded(source_id: int, excluded: bool) -> None:
    async with session_scope() as db:
        result = await db.execute(select(Source).where(Source.telegram_id == source_id))
        source = result.scalar_one_or_none()
        if source is not None:
            source.excluded = excluded
            await db.commit()
        if excluded:
            from app.models import Exclusion

            async with session_scope() as db2:
                existing = await db2.execute(
                    select(Exclusion).where(Exclusion.source_telegram_id == source_id)
                )
                if existing.scalar_one_or_none() is None:
                    db2.add(Exclusion(source_telegram_id=source_id))
                    await db2.commit()
        else:
            from app.models import Exclusion

            async with session_scope() as db2:
                existing = await db2.execute(
                    select(Exclusion).where(Exclusion.source_telegram_id == source_id)
                )
                exc = existing.scalar_one_or_none()
                if exc is not None:
                    await db2.delete(exc)
                    await db2.commit()


async def is_excluded(source_id: int) -> bool:
    async with session_scope() as db:
        result = await db.execute(
            select(Exclusion).where(Exclusion.source_telegram_id == source_id)
        )
        return result.scalar_one_or_none() is not None
