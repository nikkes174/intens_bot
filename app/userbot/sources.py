from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from sqlalchemy import select

from app.database import session_scope
from app.models import Exclusion, Source

from telethon import errors as telethon_errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import ChatInviteAlready
from telethon.utils import get_peer_id


AUDIENCE_TTL = timedelta(hours=6)
_source_cache: dict[int, dict[str, Any]] = {}
_client = None


class SourceService:
    def __init__(self, client=None):
        self.client = client

    async def refresh_audience(self, source_id: int) -> Optional[int]:
        invalidate_source_cache(source_id)
        return await get_source_audience(self.client, source_id)


def set_client(client) -> None:
    global _client
    _client = client


def get_client():
    return _client


def get_source_service() -> "SourceService":
    return SourceService(_client)


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
    if cached and now - cached["time"] < 60:
        return cached["audience"]

    source = await get_source(source_id)
    audience = source.audience if source else None
    updated_at = source.audience_updated_at if source else None
    if audience is not None and updated_at is not None:
        age = datetime.now(timezone.utc) - updated_at
        if age < AUDIENCE_TTL:
            _source_cache[source_id] = {"time": now, "audience": audience}
            return audience

    if client is not None:
        fetched = None
        try:
            entity = await client.get_entity(source_id)
            fetched = getattr(entity, "participants_count", None)
            if fetched is None:
                try:
                    participants = await client.get_participants(entity, limit=0)
                    fetched = getattr(participants, "total", None)
                except Exception:
                    fetched = None
        except Exception:
            fetched = None

        if fetched is not None:
            audience = fetched
            if source is not None:
                async with session_scope() as db:
                    db_source = await db.get(Source, source.id)
                    if db_source is not None:
                        db_source.audience = audience
                        db_source.audience_updated_at = datetime.now(timezone.utc)
                        await db.commit()
        elif source is not None:
            audience = source.audience

    _source_cache[source_id] = {"time": now, "audience": audience}
    return audience


def invalidate_source_cache(source_id: Optional[int] = None) -> None:
    if source_id is None:
        _source_cache.clear()
    else:
        _source_cache.pop(source_id, None)


async def list_sources(
    limit: Optional[int] = None, offset: int = 0, search: Optional[str] = None
) -> list[Source]:
    async with session_scope() as db:
        query = select(Source).order_by(Source.id)
        if search:
            query = query.where(Source.title.ilike(f"%{search}%"))
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        result = await db.execute(query)
        sources = list(result.scalars().all())
        for source in sources:
            db.expunge(source)
        return sources


async def count_sources(search: Optional[str] = None) -> int:
    from sqlalchemy import func

    async with session_scope() as db:
        query = select(func.count()).select_from(Source)
        if search:
            query = query.where(Source.title.ilike(f"%{search}%"))
        return int(await db.scalar(query) or 0)


async def set_source_enabled(source_id: int, enabled: bool) -> None:
    async with session_scope() as db:
        result = await db.execute(select(Source).where(Source.telegram_id == source_id))
        source = result.scalar_one_or_none()
        if source is not None:
            source.enabled = enabled
            await db.commit()


async def set_source_excluded(source_id: int, excluded: bool) -> None:
    async with session_scope() as db:
        async with db.begin():
            result = await db.execute(select(Source).where(Source.telegram_id == source_id))
            source = result.scalar_one_or_none()
            if source is not None:
                source.excluded = excluded
            from app.models import Exclusion

            existing_result = await db.execute(
                select(Exclusion).where(Exclusion.source_telegram_id == source_id)
            )
            existing = existing_result.scalar_one_or_none()
            if excluded and existing is None:
                db.add(Exclusion(source_telegram_id=source_id))
            if not excluded and existing is not None:
                await db.delete(existing)


async def is_excluded(source_id: int) -> bool:
    async with session_scope() as db:
        result = await db.execute(
            select(Exclusion).where(Exclusion.source_telegram_id == source_id)
        )
        return result.scalar_one_or_none() is not None


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_JOIN_LOCKS: dict[str, object] = {}


def _join_lock(key: str):
    import asyncio

    lock = _JOIN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _JOIN_LOCKS[key] = lock
    return lock


def parse_source_link(text: str):
    value = (text or "").strip()
    if not value:
        return None
    if value.startswith("@"):
        username = value[1:]
        if _USERNAME_RE.match(username):
            return ("public", username)
        return None

    match = re.search(r"(?:https?://)?(?:t|telegram)\.me/([^\s?#]+)", value, re.IGNORECASE)
    if not match:
        return None
    path = match.group(1).strip("/")
    if not path:
        return None
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    if segments[0].startswith("+"):
        invite_hash = segments[0][1:]
        if invite_hash:
            return ("invite", invite_hash)
        return None
    if segments[0] == "joinchat":
        if len(segments) < 2:
            return None
        invite_hash = segments[1]
        if not invite_hash or "/" in invite_hash:
            return None
        return ("invite", invite_hash)
    if len(segments) == 1:
        username = segments[0]
        if _USERNAME_RE.match(username):
            return ("public", username)
    return None


def _entity_source_type(entity) -> str:
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    return "group"


async def ensure_source_from_entity(client, entity) -> Source:
    telegram_id = get_peer_id(entity)
    title = getattr(entity, "title", None) or str(telegram_id)
    username = getattr(entity, "username", None)
    source_type = _entity_source_type(entity)

    audience = getattr(entity, "participants_count", None)
    if audience is None and client is not None:
        try:
            participants = await client.get_participants(entity, limit=0)
            audience = getattr(participants, "total", None)
        except Exception:
            audience = None

    async with session_scope() as db:
        async with db.begin():
            result = await db.execute(select(Source).where(Source.telegram_id == telegram_id))
            source = result.scalar_one_or_none()
            if source is None:
                source = Source(
                    telegram_id=telegram_id,
                    title=title,
                    username=username,
                    source_type=source_type,
                    enabled=True,
                    excluded=False,
                )
                db.add(source)
            else:
                source.title = title or source.title
                source.username = username if username is not None else source.username
                source.source_type = source_type
                source.enabled = True
                source.excluded = False

            from app.models import Exclusion

            exclusion_result = await db.execute(
                select(Exclusion).where(Exclusion.source_telegram_id == telegram_id)
            )
            exclusion = exclusion_result.scalar_one_or_none()
            if exclusion is not None:
                await db.delete(exclusion)

            if audience is not None:
                source.audience = audience
                source.audience_updated_at = datetime.now(timezone.utc)
            await db.flush()
            db.expunge(source)
    invalidate_source_cache(telegram_id)
    return source


async def join_source(client, link: str):
    parsed = parse_source_link(link)
    if parsed is None:
        raise ValueError("invalid link")
    kind, value = parsed
    lock_key = f"{kind}:{value.lower()}"
    async with _join_lock(lock_key):
        if client is None:
            raise RuntimeError("client unavailable")
        already_member = False
        if kind == "public":
            entity = await client.get_entity(value)
            if not (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
                raise ValueError("not a channel")
            try:
                await client(JoinChannelRequest(entity))
            except telethon_errors.UserAlreadyParticipantError:
                already_member = True
            except telethon_errors.InviteRequestSentError:
                raise
        else:
            invite = await client(CheckChatInviteRequest(value))
            if isinstance(invite, ChatInviteAlready):
                entity = invite.chat
                already_member = True
            else:
                try:
                    updates = await client(ImportChatInviteRequest(value))
                    entity = updates.chats[0] if getattr(updates, "chats", None) else None
                except telethon_errors.UserAlreadyParticipantError:
                    already_member = True
                    invite = await client(CheckChatInviteRequest(value))
                    entity = getattr(invite, "chat", None)
                except telethon_errors.InviteRequestSentError:
                    raise
            if entity is None:
                raise ValueError("not found")
            if not (
                getattr(entity, "broadcast", False)
                or getattr(entity, "megagroup", False)
                or getattr(entity, "id", None)
            ):
                raise ValueError("not a channel")
            if not already_member:
                try:
                    await client(JoinChannelRequest(entity))
                except telethon_errors.UserAlreadyParticipantError:
                    already_member = True
                except telethon_errors.InviteRequestSentError:
                    raise

        source = await ensure_source_from_entity(client, entity)
        source.already_member = already_member
        return source


# Compatibility aliases
join_channel = join_source
join_chat = join_source
add_source_by_link = join_source
add_channel = join_source
subscribe_to_source = join_source
