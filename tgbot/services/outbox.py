import asyncio
import json
import logging

from aiogram import Bot

from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository

logger = logging.getLogger(__name__)


class OutboxProcessor:
    def __init__(
        self,
        bot: Bot,
        outbox_repo: OutboxRepository,
        delivery_repo: DeliveryRepository,
        access_repo: AccessRepository,
        training_repo: TrainingRepository,
        user_repo: UserRepository,
        delivery_service=None,
        access_service=None,
    ):
        self.bot = bot
        self.outbox_repo = outbox_repo
        self.delivery_repo = delivery_repo
        self.access_repo = access_repo
        self.training_repo = training_repo
        self.user_repo = user_repo
        self.delivery_service = delivery_service
        self.access_service = access_service
        self._running = False

    async def start(self):
        """Start the outbox processor loop."""
        self._running = True
        while self._running:
            try:
                await self.process_events()
            except Exception as e:
                logger.exception("Error processing outbox events: %s", e)
            await asyncio.sleep(5)

    async def stop(self):
        """Stop the outbox processor."""
        self._running = False

    async def process_events(self):
        """Process pending outbox events."""
        events = await self.outbox_repo.get_pending_events(limit=50)
        for event in events:
            try:
                if event.event_type == "payment_succeeded":
                    await self._handle_payment_succeeded(event.payload)
                elif event.event_type == "deliver_materials":
                    await self._handle_deliver_materials(event.payload)
                elif event.event_type == "send_invite":
                    await self._handle_send_invite(event.payload)
                else:
                    logger.warning("Unknown event type: %s", event.event_type)

                await self.outbox_repo.mark_processed(event.id)
            except Exception as e:
                logger.exception("Failed to process event %s: %s", event.id, e)
                await self.outbox_repo.mark_failed(event.id, max_attempts=event.max_attempts)

    async def _handle_payment_succeeded(self, payload: str):
        """Handle payment_succeeded event."""
        data = json.loads(payload)
        user_id = data["user_id"]
        training_id = data["training_id"]

        # Deliver materials
        if self.delivery_service:
            await self.delivery_service.deliver_materials(
                user_id,
                training_id,
                self.delivery_repo,
                self.training_repo,
                self.training_repo,
            )

        # Send invite link
        if self.access_service:
            invite_link = await self.access_service.create_invite_link(
                user_id,
                training_id,
                self.training_repo,
                self.training_repo,
            )
            if invite_link:
                await self.bot.send_message(
                    user_id,
                    f"🔐 Ваша ссылка на вступление в группу (действует 24 часа):\n{invite_link}",
                )

    async def _handle_deliver_materials(self, payload: str):
        """Handle deliver_materials event."""
        data = json.loads(payload)
        if self.delivery_service:
            await self.delivery_service.deliver_materials(
                data["user_id"],
                data["training_id"],
                self.delivery_repo,
                self.training_repo,
                self.training_repo,
            )

    async def _handle_send_invite(self, payload: str):
        """Handle send_invite event."""
        data = json.loads(payload)
        if self.access_service:
            invite_link = await self.access_service.create_invite_link(
                data["user_id"],
                data["training_id"],
                self.training_repo,
                self.training_repo,
            )
            if invite_link:
                await self.bot.send_message(
                    data["user_id"],
                    f"🔐 Ваша ссылка на вступление в группу:\n{invite_link}",
                )
