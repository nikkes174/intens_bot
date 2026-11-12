from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.database import session_scope
from app.models import WebDelivery
from app.services.monitor import notifications_enabled
from app.services.notifier import send_notification
from app.services.web_monitor.formatter import format_web_notification, web_article_keyboard
from app.services.web_monitor.storage import (
    get_article_watch_context,
    get_delivery_context,
    recover_web_deliveries,
)

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[int] | None = None
_queued_ids: set[int] = set()
_delivery_locks: dict[int, asyncio.Lock] = {}
_article_locks: dict[int, asyncio.Lock] = {}
_bot = None
_admin_ids: tuple[int, ...] | None = None


def configure(
    bot=None,
    admin_id: int | None = None,
    admin_ids: tuple[int, ...] | None = None,
) -> None:
    global _bot, _admin_ids
    if bot is not None:
        _bot = bot
    if admin_ids is None and admin_id is not None:
        admin_ids = (admin_id,)
    if admin_ids is not None:
        _admin_ids = tuple(dict.fromkeys(item for item in admin_ids if item > 0))


def get_queue() -> asyncio.Queue[int]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=max(1, get_settings().queue_size))
    return _queue


def _lock_for(mapping: dict[int, asyncio.Lock], key: int) -> asyncio.Lock:
    lock = mapping.get(key)
    if lock is None:
        lock = asyncio.Lock()
        mapping[key] = lock
    return lock


async def enqueue_delivery(delivery_id: int) -> None:
    if delivery_id in _queued_ids:
        return
    _queued_ids.add(delivery_id)
    try:
        await get_queue().put(delivery_id)
    except asyncio.CancelledError:
        _queued_ids.discard(delivery_id)
        raise


async def enqueue_pending_deliveries() -> None:
    for delivery_id in await recover_web_deliveries():
        await enqueue_delivery(delivery_id)


async def _claim(delivery_id: int):
    now = datetime.now(UTC)
    async with session_scope() as db:
        delivery = await db.get(WebDelivery, delivery_id)
        if delivery is None or delivery.status == "sent":
            return None
        retry_at = delivery.next_retry_at
        if retry_at is not None:
            retry_at = retry_at if retry_at.tzinfo else retry_at.replace(tzinfo=UTC)
            if retry_at > now:
                return None
        started = delivery.processing_started_at
        if started is not None:
            started = started if started.tzinfo else started.replace(tzinfo=UTC)
            if delivery.status == "processing" and now - started < timedelta(minutes=5):
                return None
        delivery.status = "processing"
        delivery.processing_started_at = now
        delivery.attempts += 1
        delivery.next_retry_at = None
        attempt = delivery.attempts
        admin_id = delivery.admin_id
        await db.commit()
    return attempt, admin_id


async def _mark_failure(delivery_id: int, attempt: int, error: Exception, retry_after: float | None = None) -> None:
    settings = get_settings()
    async with session_scope() as db:
        delivery = await db.get(WebDelivery, delivery_id)
        if delivery is None:
            return
        if attempt >= settings.max_attempts:
            delivery.status = "failed"
            delivery.next_retry_at = None
        else:
            delay = retry_after if retry_after is not None else min(2**attempt, 300)
            delivery.status = "pending"
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        delivery.last_error = str(error)
        delivery.processing_started_at = None
        await db.commit()


async def process_delivery(delivery_id: int) -> bool:
    async with _lock_for(_delivery_locks, delivery_id):
        claimed = await _claim(delivery_id)
        if claimed is None:
            return False
        attempt, delivery_admin_id = claimed
        context = await get_delivery_context(delivery_id)
        if context is None:
            await _mark_failure(delivery_id, attempt, RuntimeError("Missing web delivery context"))
            return False
        _, _, article, _ = context
        article_lock = _lock_for(_article_locks, article.id)
        async with article_lock:
            # A second detection for the same article is grouped into the
            # first successful message and then marked delivered as a unit.
            grouped = await get_article_watch_context(article.id, delivery_admin_id)
            if grouped is None:
                await _mark_failure(delivery_id, attempt, RuntimeError("Missing article context"))
                return False
            article, rows = grouped
            queries = [row[1].query for row in rows if row[1] is not None]
            watch_types = [
                row[1].watch_type
                for row in rows
                if row[1] is not None
            ]
            matched_texts = [row[0].matched_text for row in rows if row[0] is not None]
            bot = _bot
            if bot is None:
                await _mark_failure(delivery_id, attempt, RuntimeError("Bot is not configured"))
                return False
            try:
                if not await notifications_enabled():
                    # The discovery remains in the database and no send is
                    # attempted while the global notification switch is off.
                    await _mark_failure(
                        delivery_id,
                        attempt,
                        RuntimeError("Notifications are disabled"),
                        retry_after=300,
                    )
                    return False
                text = format_web_notification(
                    article,
                    queries,
                    detected_at=next(
                        (row[2].created_at for row in rows if row[2] is not None),
                        None,
                    ),
                    timezone_name=get_settings().timezone,
                    matched_texts=matched_texts,
                    watch_types=watch_types,
                )
                await send_notification(
                    bot,
                    delivery_admin_id,
                    text,
                    web_article_keyboard(article.url),
                )
            except Exception as exc:  # noqa: BLE001
                retry_after = getattr(exc, "retry_after", None)
                await _mark_failure(delivery_id, attempt, exc, retry_after=retry_after)
                logger.warning("Web delivery %s failed: %s", delivery_id, exc)
                return False

            detection_ids = [row[0].id for row in rows]
            async with session_scope() as db:
                result = await db.execute(
                    select(WebDelivery).where(
                        WebDelivery.admin_id == delivery_admin_id,
                        WebDelivery.detection_id.in_(detection_ids),
                    )
                )
                now = datetime.now(UTC)
                for delivery in result.scalars().all():
                    delivery.status = "sent"
                    delivery.delivered_at = now
                    delivery.processing_started_at = None
                    delivery.last_error = None
                    delivery.next_retry_at = None
                await db.commit()
            return True


async def web_delivery_worker() -> None:
    await enqueue_pending_deliveries()
    queue = get_queue()
    while True:
        delivery_id = await queue.get()
        try:
            await process_delivery(delivery_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive guard for worker lifetime
            logger.exception("Web delivery worker error")
        finally:
            _queued_ids.discard(delivery_id)
            queue.task_done()


async def recover_pending_web_deliveries() -> None:
    while True:
        try:
            await enqueue_pending_deliveries()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web delivery recovery failed")
        await asyncio.sleep(10)


# Names matching the Telegram monitor's worker terminology make integration
# and external health checks straightforward.
notification_worker = web_delivery_worker
_process_delivery = process_delivery
web_notification_worker = web_delivery_worker
