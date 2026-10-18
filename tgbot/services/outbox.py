import asyncio
import html
import json
import logging

from aiogram import Bot

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.handlers.handlers_texts import (
    PAYMENT_CANCELED_TEXT,
    PURCHASE_SUCCESS_NO_MATERIALS_TEXT,
    PURCHASE_SUCCESS_PENDING_TEXT,
    PURCHASE_SUCCESS_TEXT,
)
from tgbot.keyboards.inline import cancellation_keyboard, invite_keyboard
from tgbot.services.access import AccessService
from tgbot.services.delivery import DeliveryService

logger = logging.getLogger(__name__)


class OutboxProcessor:
    def __init__(self, bot: Bot, admin_ids: list[int] | None = None):
        self.bot = bot
        self.admin_ids = admin_ids or []
        self._running = False

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.process_events()
            except Exception:
                logger.exception("Error processing outbox events")
            await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False

    async def process_events(self) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            outbox_repo = OutboxRepository(session)
            events = await outbox_repo.get_pending_events(limit=50)
            for event in events:
                try:
                    if event.event_type == "payment_succeeded":
                        await self._handle_payment_succeeded(json.loads(event.payload))
                    elif event.event_type == "payment_canceled":
                        await self._handle_payment_canceled(json.loads(event.payload))
                    else:
                        logger.warning("Unknown outbox event type: %s", event.event_type)
                    await outbox_repo.mark_processed(event.id)
                except Exception as error:
                    logger.exception("Failed to process outbox event %s: %s", event.id, error)
                    await outbox_repo.mark_failed(event.id, max_attempts=5)

    async def _handle_payment_succeeded(self, data: dict) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            user = await UserRepository(session).get_by_id(data["user_id"])
            order_repo = OrderRepository(session)
            order = await order_repo.get_by_id(data["order_id"])
            training_repo = TrainingRepository(session)
            material_repo = MaterialRepository(session)
            delivery_repo = DeliveryRepository(session)
            invite_repo = InviteRepository(session)
            if not user or not order:
                raise RuntimeError("Purchase user or order is missing")

            product_ids = data.get("product_ids") or [order.training_id]
            purchased_names = []
            for product_id in product_ids:
                product = await training_repo.get_by_id(product_id)
                if product:
                    purchased_names.append(html.escape(product.title))
            product_name = " + ".join(purchased_names) or "твоя программа"
            training_ids = data.get("training_ids") or await training_repo.get_component_ids(
                order.training_id
            )
            missing_materials = []
            missing_channels = []
            for training_id in training_ids:
                product = await training_repo.get_by_id(training_id)
                materials = await material_repo.get_by_training(training_id)
                sendable_materials = [
                    material
                    for material in materials
                    if (material.material_type == "text" and material.text_content)
                    or (
                        material.material_type in {"photo", "video", "document", "pdf", "audio"}
                        and material.telegram_file_id
                    )
                ]
                if not product or not sendable_materials:
                    missing_materials.append(product.title if product else f"#{training_id}")
                if not product or not product.telegram_chat_id:
                    missing_channels.append(product.title if product else f"#{training_id}")

            if order.delivery_status == "pending":
                if missing_materials and not missing_channels:
                    text_template = PURCHASE_SUCCESS_NO_MATERIALS_TEXT
                elif missing_materials or missing_channels:
                    text_template = PURCHASE_SUCCESS_PENDING_TEXT
                else:
                    text_template = PURCHASE_SUCCESS_TEXT
                await self.bot.send_message(
                    user.telegram_user_id,
                    text_template.format(product_name=product_name),
                )

            delivery_service = DeliveryService(self.bot)
            delivery_failed = False
            for training_id in training_ids:
                materials = await material_repo.get_by_training(training_id)
                if not materials:
                    continue
                result = await delivery_service.deliver_materials(
                    user.id,
                    training_id,
                    delivery_repo,
                    material_repo,
                    training_repo,
                )
                if result.get("failed"):
                    delivery_failed = True

            access_service = AccessService(self.bot)
            invite_failed = False
            for training_id in training_ids:
                product = await training_repo.get_by_id(training_id)
                if not product or not product.telegram_chat_id:
                    continue
                invite_link = await access_service.create_invite_link(
                    user.id,
                    training_id,
                    training_repo,
                    invite_repo,
                )
                if invite_link:
                    invite_text = (
                        f"🎥 Видео с техникой упражнений — в канале «{html.escape(product.title)}»."
                    )
                    await self.bot.send_message(
                        user.telegram_user_id,
                        invite_text,
                        reply_markup=invite_keyboard(invite_link),
                        parse_mode="HTML",
                    )
                else:
                    invite_failed = True

            needs_attention = bool(
                missing_materials or missing_channels or delivery_failed or invite_failed
            )
            prior_status = order.delivery_status
            order.delivery_status = "attention" if needs_attention else "sent"
            await session.commit()

            if needs_attention and prior_status != "attention":
                details = ", ".join(missing_materials + missing_channels) or (
                    "не удалось отправить часть материалов или ссылок"
                )
                for admin_id in self.admin_ids:
                    try:
                        await self.bot.send_message(
                            admin_id,
                            f"⚠️ Нужна проверка выдачи заказа #{order.id}: {details}.",
                        )
                    except Exception:
                        logger.exception(
                            "Could not notify admin %s about order %s", admin_id, order.id
                        )

    async def _handle_payment_canceled(self, data: dict) -> None:
        session_factory = get_session_factory()
        async with session_factory() as session:
            user = await UserRepository(session).get_by_id(data["user_id"])
            if not user:
                return
            await self.bot.send_message(
                user.telegram_user_id,
                PAYMENT_CANCELED_TEXT,
                reply_markup=cancellation_keyboard(data.get("product_code")),
            )
