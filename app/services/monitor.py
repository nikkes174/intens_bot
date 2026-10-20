from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import session_scope
from app.models import Detection, Delivery, Mention, Setting
from app.services.formatter import format_notification
from app.services.matcher import find_mentions, normalize
from app.services.notifier import send_notification
from app.userbot import sources as source_service

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[int] | None = None
_bot = None
_admin_id: Optional[int] = None
_client = None


def configure(bot=None, admin_id=None, client=None, settings=None):
    global _bot, _admin_id, _client
    if bot is not None:
        _bot = bot
    if admin_id is not None:
        _admin_id = admin_id
    if client is not None:
        _client = client


def get_queue() -> asyncio.Queue[int]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=get_settings().queue_size)
    return _queue


async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    async with session_scope() as db:
        setting = await db.get(Setting, key)
        if setting is not None:
            return setting.value
    settings = get_settings()
    if key == "monitoring_enabled":
        return "true" if settings.monitoring_enabled else "false"
    if key == "source_mode":
        return settings.source_mode
    if key == "timezone":
        return settings.timezone
    return default


async def set_setting(key: str, value: str) -> None:
    async with session_scope() as db:
        setting = await db.get(Setting, key)
        if setting is None:
            setting = Setting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
            setting.updated_at = datetime.now(timezone.utc)
        await db.commit()


async def monitoring_enabled() -> bool:
    value = await get_setting("monitoring_enabled", None)
    if value is None:
        return get_settings().monitoring_enabled
    return str(value).lower() in {"1", "true", "yes", "on"}


async def source_allowed(source_id: int) -> bool:
    mode = await get_setting("source_mode", get_settings().source_mode)
    if await source_service.is_excluded(source_id):
        return False
    if mode == "selected":
        source = await source_service.get_source(source_id)
        return bool(source and source.enabled)
    return True


async def get_active_keywords() -> list[str]:
    async with session_scope() as db:
        result = await db.execute(select(Mention).where(Mention.enabled.is_(True)))
        keywords = [item.keyword for item in result.scalars().all()]
    seen: set[str] = set()
    unique: list[str] = []
    for keyword in keywords:
        normalized = normalize(keyword)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(keyword)
    return unique


async def save_detection(source_id: int, message_id: int, matches: list[str]) -> bool:
    async with session_scope() as db:
        detection = Detection(
            source_id=source_id,
            message_id=message_id,
            matched_keywords=", ".join(matches),
        )
        db.add(detection)
        try:
            await db.commit()
            return True
        except IntegrityError:
            await db.rollback()
            return False


async def enqueue_notification(
    event=None,
    matches: Optional[list[str]] = None,
    source_id: Optional[int] = None,
    message_id: Optional[int] = None,
    text: Optional[str] = None,
) -> Optional[int]:
    if matches is None:
        matches = []
    if source_id is None and event is not None:
        source_id = getattr(event, "chat_id", None)
    if message_id is None and event is not None:
        message_id = getattr(event.message, "id", None)
    if text is None and event is not None:
        text = getattr(event, "raw_text", None)

    if source_id is None or message_id is None:
        return None

    title = await source_service.get_source_title(source_id)
    username = await source_service.get_source_username(source_id)
    source_obj = await source_service.get_source(source_id)
    audience = await source_service.get_source_audience(_client, source_id)
    url = source_service.source_post_url(source_obj, message_id)
    if url is None and username:
        url = source_service.public_post_url(username, message_id)

    published_at = datetime.now(timezone.utc)
    if event is not None and getattr(event, "date", None) is not None:
        published_at = event.date
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)

    timezone_name = await get_setting("timezone", get_settings().timezone)
    if timezone_name is None:
        timezone_name = get_settings().timezone

    payload = {
        "source_id": source_id,
        "message_id": message_id,
        "matches": matches,
        "text": text or "",
        "title": title,
        "published_at": published_at.isoformat(),
        "audience": audience,
        "url": url,
        "timezone": timezone_name,
    }

    async with session_scope() as db:
        detection_result = await db.execute(
            select(Detection).where(
                Detection.source_id == source_id,
                Detection.message_id == message_id,
            )
        )
        detection = detection_result.scalar_one_or_none()
        if detection is None:
            return None
        delivery = Delivery(
            detection_id=detection.id,
            status="pending",
            payload=json.dumps(payload, ensure_ascii=False),
        )
        db.add(delivery)
        await db.commit()
        await db.refresh(delivery)
        delivery_id = delivery.id

    await get_queue().put(delivery_id)
    return delivery_id


async def process_message(
    source_id: int,
    message_id: int,
    text: str,
    event=None,
    **kwargs,
) -> bool:
    if not await monitoring_enabled():
        return False
    if not await source_allowed(source_id):
        return False

    keywords = await get_active_keywords()
    matches = find_mentions(text, keywords)
    if not matches:
        return False

    created = await save_detection(
        source_id=source_id,
        message_id=message_id,
        matches=matches,
    )
    if not created:
        return False

    await enqueue_notification(
        event=event,
        matches=matches,
        source_id=source_id,
        message_id=message_id,
        text=text,
    )
    return True


async def _process_delivery(delivery_id: int) -> None:
    settings = get_settings()
    async with session_scope() as db:
        delivery = await db.get(Delivery, delivery_id)
        if delivery is None or delivery.status == "sent":
            return
        delivery.attempts += 1
        attempt = delivery.attempts
        payload_raw = delivery.payload
        await db.commit()

    if not payload_raw:
        return

    payload = json.loads(payload_raw)
    published_at = datetime.fromisoformat(payload["published_at"])
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    text = format_notification(
        title=payload["title"],
        published_at=published_at,
        audience=payload["audience"],
        matches=payload["matches"],
        text=payload["text"],
        url=payload["url"],
        timezone_name=payload.get("timezone", settings.timezone),
    )

    admin_id = _admin_id
    if admin_id is None and settings.admin_ids:
        admin_id = settings.admin_ids[0]

    last_error = None
    for _ in range(1):
        try:
            if _bot is None or admin_id is None:
                raise RuntimeError("Bot or admin id is not configured")
            await send_notification(_bot, admin_id, text, None)
            async with session_scope() as db:
                delivery = await db.get(Delivery, delivery_id)
                if delivery is not None:
                    delivery.status = "sent"
                    delivery.delivered_at = datetime.now(timezone.utc)
                    delivery.last_error = None
                    await db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("Notification delivery %s failed: %s", delivery_id, exc)
            if "FloodWait" in type(exc).__name__ or "RetryAfter" in type(exc).__name__:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None:
                    retry_after = getattr(exc, "seconds", 1)
                await asyncio.sleep(retry_after)
                # retry once after flood wait
                try:
                    if _bot is None or admin_id is None:
                        raise RuntimeError("Bot or admin id is not configured")
                    await send_notification(_bot, admin_id, text, None)
                    async with session_scope() as db:
                        delivery = await db.get(Delivery, delivery_id)
                        if delivery is not None:
                            delivery.status = "sent"
                            delivery.delivered_at = datetime.now(timezone.utc)
                            delivery.last_error = None
                            await db.commit()
                    return
                except Exception as exc2:  # noqa: BLE001
                    last_error = str(exc2)
            break

    async with session_scope() as db:
        delivery = await db.get(Delivery, delivery_id)
        if delivery is not None:
            delivery.status = (
                "failed" if attempt >= settings.max_attempts else "pending"
            )
            delivery.last_error = last_error
            if delivery.status == "pending":
                delivery.next_retry_at = datetime.now(timezone.utc)
            await db.commit()

    if attempt < settings.max_attempts:
        await asyncio.sleep(min(2**attempt, 30))
        await get_queue().put(delivery_id)


async def notification_worker() -> None:
    queue = get_queue()
    while True:
        delivery_id = await queue.get()
        try:
            await _process_delivery(delivery_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Notification worker error: %s", exc)
        finally:
            queue.task_done()


async def recover_pending_notifications() -> None:
    async with session_scope() as db:
        result = await db.execute(
            select(Delivery).where(Delivery.status.in_(["pending", "failed"]))
        )
        deliveries = result.scalars().all()
    for delivery in deliveries:
        if delivery.attempts >= get_settings().max_attempts:
            continue
        await get_queue().put(delivery.id)
