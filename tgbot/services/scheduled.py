import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.handlers.handlers_texts import (
    CARD_REMINDER_TEXT,
    FOLLOWUP_3_DAYS_TEXT,
    FOLLOWUP_7_DAYS_TEXT,
)
from tgbot.keyboards.inline import (
    card_reminder_keyboard,
    followup_3_days_keyboard,
    followup_7_days_keyboard,
)

logger = logging.getLogger(__name__)


class ScheduledMessageProcessor:
    """Poll persistent jobs; all jobs survive bot or container restarts."""

    def __init__(self, bot: Bot, interval_seconds: int = 30):
        self.bot = bot
        self.interval_seconds = interval_seconds
        self._running = False

    async def start(self) -> None:
        self._running = True
        session_factory = get_session_factory()
        async with session_factory() as session:
            recovered = await ScheduledMessageRepository(session).recover_processing()
            if recovered:
                logger.warning("Marked %d interrupted scheduled messages as failed", recovered)

        while self._running:
            try:
                await self.process_due()
            except Exception:
                logger.exception("Scheduled-message processing failed")
            await asyncio.sleep(self.interval_seconds)

    async def stop(self) -> None:
        self._running = False

    async def process_due(self, now: datetime | None = None) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            scheduled_repo = ScheduledMessageRepository(session)
            jobs = await scheduled_repo.get_due(now=now)

        for job in jobs:
            async with session_factory() as session:
                scheduled_repo = ScheduledMessageRepository(session)
                if not await scheduled_repo.claim(job.id):
                    continue

                user = await UserRepository(session).get_by_id(job.user_id)
                training_repo = TrainingRepository(session)
                access_repo = AccessRepository(session)
                order_repo = OrderRepository(session)
                if not user:
                    await scheduled_repo.mark_skipped(job.id, "User record no longer exists")
                    continue

                try:
                    prepared = await self._prepare_message(
                        job,
                        training_repo,
                        access_repo,
                        order_repo,
                        scheduled_repo,
                        now,
                    )
                    if prepared is None:
                        continue
                    text, reply_markup = prepared
                    await self.bot.send_message(
                        user.telegram_user_id,
                        text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                    await scheduled_repo.mark_sent(job.id, now=now)
                except (TelegramForbiddenError, TelegramBadRequest) as error:
                    # Blocked bot and invalid recipient errors will never recover by retrying.
                    await scheduled_repo.mark_failed(job.id, str(error), retry=False)
                    logger.info("Scheduled message %s cannot be delivered: %s", job.id, error)
                except TelegramAPIError as error:
                    await scheduled_repo.mark_failed(job.id, str(error), retry=True)
                    logger.warning("Temporary Telegram error for scheduled message %s", job.id)
                except Exception as error:
                    await scheduled_repo.mark_failed(job.id, str(error), retry=True)
                    logger.exception("Scheduled message %s failed", job.id)

    async def _prepare_message(
        self,
        job,
        training_repo: TrainingRepository,
        access_repo: AccessRepository,
        order_repo: OrderRepository,
        scheduled_repo: ScheduledMessageRepository,
        now: datetime | None,
    ):
        current = now or datetime.utcnow()
        if job.message_type == "card_view_24h":
            training = await training_repo.get_by_id(job.training_id) if job.training_id else None
            if not training or not training.is_active:
                await scheduled_repo.mark_skipped(job.id, "Product is no longer active")
                return None

            owned_ids = {
                access.training_id for access in await access_repo.get_user_accesses(job.user_id)
            }
            if training.is_bundle:
                component_ids = await training_repo.get_component_ids(training.id)
                purchased = bool(component_ids) and all(
                    item_id in owned_ids for item_id in component_ids
                )
            else:
                purchased = training.id in owned_ids
            if purchased:
                await scheduled_repo.mark_skipped(job.id, "Product purchased before reminder")
                return None
            if await scheduled_repo.has_recent_view_reminder(job.user_id, now=current):
                await scheduled_repo.mark_skipped(
                    job.id, "Another product reminder was sent in the last 24 hours"
                )
                return None
            return (
                CARD_REMINDER_TEXT.format(product_name=html.escape(training.title)),
                card_reminder_keyboard(training.product_code or "glute"),
            )

        order = await order_repo.get_by_id(job.order_id) if job.order_id else None
        if not order or order.status != "succeeded":
            await scheduled_repo.mark_skipped(job.id, "Order is not a confirmed purchase")
            return None

        if job.message_type == "followup_72h":
            return FOLLOWUP_3_DAYS_TEXT, followup_3_days_keyboard()

        if job.message_type == "followup_7d":
            owned_ids = {
                access.training_id for access in await access_repo.get_user_accesses(job.user_id)
            }
            products = [
                await training_repo.get_by_code(code) for code in ("glute", "vshape", "core")
            ]
            if all(product is not None and product.id in owned_ids for product in products):
                await scheduled_repo.mark_skipped(job.id, "All programs already owned")
                return None
            return FOLLOWUP_7_DAYS_TEXT, followup_7_days_keyboard()

        await scheduled_repo.mark_failed(job.id, "Unknown message type", retry=False)
        return None
