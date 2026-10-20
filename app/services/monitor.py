from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import session_scope
from app.models import (
    Delivery,
    Detection,
    DetectionMention,
    Mention,
    Setting,
    StopWord,
)
from app.services.formatter import format_notification
from app.services.matcher import find_mentions, normalize
from app.services.notifier import send_notification
from app.userbot import sources as source_service

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[int] | None = None
_bot = None
_admin_id: Optional[int] = None
_client = None
_source_service = None


def configure(bot=None, admin_id=None, client=None, settings=None, source_service=None):
    global _bot, _admin_id, _client, _source_service
    if bot is not None:
        _bot = bot
    if admin_id is not None:
        _admin_id = admin_id
    if client is not None:
        _client = client
    if source_service is not None:
        _source_service = source_service


def get_source_service():
    global _source_service
    if _source_service is None:
        _source_service = (
            source_service.SourceService(_client)
            if hasattr(source_service, "SourceService")
            else None
        )
    return _source_service


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
    defaults = {
        "monitoring_enabled": "true" if settings.monitoring_enabled else "false",
        "notifications_enabled": "true",
        "source_mode": settings.source_mode,
        "timezone": settings.timezone,
    }
    if key in defaults:
        return defaults[key]
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


async def notifications_enabled() -> bool:
    value = await get_setting("notifications_enabled", "true")
    return str(value).lower() in {"1", "true", "yes", "on"}


async def source_allowed(source_id: int) -> bool:
    mode = await get_setting("source_mode", get_settings().source_mode)
    if await source_service.is_excluded(source_id):
        return False
    if mode == "selected":
        source = await source_service.get_source(source_id)
        return bool(source and source.enabled)
    return True


async def get_active_mentions() -> list[Mention]:
    async with session_scope() as db:
        result = await db.execute(select(Mention).where(Mention.enabled.is_(True)))
        mentions = list(result.scalars().all())
        for mention in mentions:
            db.expunge(mention)
        return mentions


async def get_active_keywords() -> list[str]:
    mentions = await get_active_mentions()
    seen: set[str] = set()
    unique: list[str] = []
    for mention in mentions:
        normalized = normalize(mention.keyword)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(mention.keyword)
    return unique


async def get_stop_words() -> list[str]:
    async with session_scope() as db:
        result = await db.execute(select(StopWord).where(StopWord.enabled.is_(True)))
        return [item.word for item in result.scalars().all()]


def _contains_stop_word(text: str, stop_words: list[str]) -> bool:
    normalized_text = normalize(text or "")
    for word in stop_words:
        if normalize(word) and normalize(word) in normalized_text:
            return True
    return False


async def _prepare_payload(
    source_id: int,
    message_id: int,
    matches: list[str],
    text: str,
    event,
) -> dict:
    title = await source_service.get_source_title(source_id)
    username = await source_service.get_source_username(source_id)
    source_obj = await source_service.get_source(source_id)
    try:
        audience = await source_service.get_source_audience(_client, source_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Audience fetch failed: %s", exc)
        audience = source_obj.audience if source_obj else None
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

    return {
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


async def create_detection_and_delivery(
    source_id: int,
    message_id: int,
    matches: list[str],
    mention_ids: list[int],
    payload: dict,
) -> Optional[int]:
    try:
        async with session_scope() as db:
            async with db.begin():
                detection = Detection(
                    source_id=source_id,
                    message_id=message_id,
                    matched_keywords=json.dumps(matches, ensure_ascii=False),
                )
                db.add(detection)
                await db.flush()
                for mention_id in mention_ids:
                    db.add(
                        DetectionMention(
                            detection_id=detection.id,
                            mention_id=mention_id,
                        )
                    )
                delivery = Delivery(
                    detection_id=detection.id,
                    status="pending",
                    payload=json.dumps(payload, ensure_ascii=False),
                )
                db.add(delivery)
                await db.flush()
                delivery_id = delivery.id
        await _enqueue_delivery_id(delivery_id)
        return delivery_id
    except IntegrityError:
        await _recover_existing_delivery(source_id, message_id)
        return None


async def _recover_existing_delivery(source_id: int, message_id: int) -> None:
    async with session_scope() as db:
        result = await db.execute(
            select(Detection).where(
                Detection.source_id == source_id,
                Detection.message_id == message_id,
            )
        )
        detection = result.scalar_one_or_none()
        if detection is None:
            return
        delivery_result = await db.execute(
            select(Delivery).where(Delivery.detection_id == detection.id)
        )
        delivery = delivery_result.scalar_one_or_none()
        if delivery is not None and delivery.status != "sent":
            await _enqueue_delivery_id(delivery.id)


async def _enqueue_delivery_id(delivery_id: int) -> None:
    queue = get_queue()
    try:
        queue.put_nowait(delivery_id)
    except asyncio.QueueFull:
        asyncio.create_task(_wait_and_enqueue(delivery_id))


async def _wait_and_enqueue(delivery_id: int) -> None:
    await get_queue().put(delivery_id)


async def save_detection(source_id: int, message_id: int, matches: list[str]) -> bool:
    # Compatibility helper. Prefer create_detection_and_delivery.
    try:
        async with session_scope() as db:
            async with db.begin():
                detection = Detection(
                    source_id=source_id,
                    message_id=message_id,
                    matched_keywords=json.dumps(matches, ensure_ascii=False),
                )
                db.add(detection)
                await db.flush()
        return True
    except IntegrityError:
        return False


async def enqueue_notification(
    event=None,
    matches: Optional[list[str]] = None,
    source_id: Optional[int] = None,
    message_id: Optional[int] = None,
    text: Optional[str] = None,
    mention_ids: Optional[list[int]] = None,
) -> Optional[int]:
    if matches is None:
        matches = []
    if mention_ids is None:
        mention_ids = []
    if source_id is None and event is not None:
        source_id = getattr(event, "chat_id", None)
    if message_id is None and event is not None:
        message_id = getattr(event.message, "id", None)
    if text is None and event is not None:
        text = getattr(event, "raw_text", None)

    if source_id is None or message_id is None:
        return None

    payload = await _prepare_payload(source_id, message_id, matches, text or "", event)
    return await create_detection_and_delivery(
        source_id=source_id,
        message_id=message_id,
        matches=matches,
        mention_ids=mention_ids,
        payload=payload,
    )


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

    stop_words = await get_stop_words()
    if _contains_stop_word(text, stop_words):
        return False

    keywords = await get_active_keywords()
    matches = find_mentions(text, keywords)
    mentions = await get_active_mentions()
    keyword_to_mention = {normalize(m.keyword): m for m in mentions}
    if not matches:
        return False

    if not await notifications_enabled():
        # Still record detection for statistics, but do not create delivery.
        try:
            async with session_scope() as db:
                async with db.begin():
                    detection = Detection(
                        source_id=source_id,
                        message_id=message_id,
                        matched_keywords=json.dumps(matches, ensure_ascii=False),
                    )
                    db.add(detection)
                    await db.flush()
                    for keyword in matches:
                        mention = keyword_to_mention.get(normalize(keyword))
                        if mention is not None:
                            db.add(
                                DetectionMention(
                                    detection_id=detection.id,
                                    mention_id=mention.id,
                                )
                            )
        except IntegrityError:
            return False
        return True

    mention_ids = []
    for keyword in matches:
        mention = keyword_to_mention.get(normalize(keyword))
        if mention is not None:
            mention_ids.append(mention.id)

    delivery_id = await enqueue_notification(
        event=event,
        matches=matches,
        source_id=source_id,
        message_id=message_id,
        text=text,
        mention_ids=mention_ids,
    )
    return delivery_id is not None


async def _process_delivery(delivery_id: int) -> None:
    settings = get_settings()
    async with session_scope() as db:
        delivery = await db.get(Delivery, delivery_id)
        if delivery is None or delivery.status == "sent":
            return
        if delivery.next_retry_at is not None and delivery.next_retry_at > datetime.now(
            timezone.utc
        ):
            delay = (
                delivery.next_retry_at - datetime.now(timezone.utc)
            ).total_seconds()
            asyncio.create_task(_retry_after_delay(delivery_id, delay))
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

    from app.admin.keyboards import notification_keyboard

    text = format_notification(
        title=payload["title"],
        published_at=published_at,
        audience=payload["audience"],
        matches=payload["matches"],
        text=payload["text"],
        url=payload["url"],
        timezone_name=payload.get("timezone", settings.timezone),
    )
    keyboard = notification_keyboard(payload.get("url"), payload.get("source_id"))

    admin_id = _admin_id
    if admin_id is None:
        admin_id = settings.admin_id

    try:
        if _bot is None or admin_id is None or admin_id == 0:
            raise RuntimeError("Bot or admin id is not configured")
        await send_notification(_bot, admin_id, text, keyboard)
        async with session_scope() as db:
            delivery = await db.get(Delivery, delivery_id)
            if delivery is not None:
                delivery.status = "sent"
                delivery.delivered_at = datetime.now(timezone.utc)
                delivery.last_error = None
                await db.commit()
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notification delivery %s failed: %s", delivery_id, exc)
        retry_after = None
        if "RetryAfter" in type(exc).__name__ or "FloodWait" in type(exc).__name__:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is None:
                retry_after = getattr(exc, "seconds", 1)
        async with session_scope() as db:
            delivery = await db.get(Delivery, delivery_id)
            if delivery is not None:
                if attempt >= settings.max_attempts:
                    delivery.status = "failed"
                else:
                    delivery.status = "pending"
                    delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(
                        seconds=retry_after if retry_after else min(2**attempt, 30)
                    )
                delivery.last_error = str(exc)
                await db.commit()
        if attempt < settings.max_attempts:
            delay = retry_after if retry_after else min(2**attempt, 30)
            asyncio.create_task(_retry_after_delay(delivery_id, delay))


async def _retry_after_delay(delivery_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    await _enqueue_delivery_id(delivery_id)


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
        await _enqueue_delivery_id(delivery.id)
