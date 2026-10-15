import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tgbot.db.models import TrainingMaterialModel
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.training import TrainingRepository

logger = logging.getLogger(__name__)


class DeliveryService:
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds

    def __init__(self, bot: Bot):
        self.bot = bot

    async def deliver_materials(
        self,
        user_id: int,
        training_id: int,
        delivery_repo: DeliveryRepository,
        material_repo: MaterialRepository,
        training_repo: TrainingRepository,
    ) -> dict:
        """Deliver all materials for a training to a user."""
        training = await training_repo.get_by_id(training_id)
        if not training:
            logger.error("Training %s not found", training_id)
            return {"success": False, "error": "Training not found"}

        materials = await material_repo.get_by_training(training_id)
        if not materials:
            logger.info("No materials for training %s", training_id)
            return {"success": True, "delivered": 0}

        delivered = 0
        failed = 0

        for material in materials:
            # Check if already sent
            existing = await delivery_repo.get_delivery(user_id, material.id)
            if existing and existing.status == "sent":
                continue

            # Create delivery record if not exists
            if not existing:
                existing = await delivery_repo.create(user_id, training_id, material.id)

            # Send with retries
            for attempt in range(self.MAX_RETRIES):
                try:
                    await self._send_material(user_id, material)
                    await delivery_repo.mark_sent(existing.id)
                    delivered += 1
                    break
                except TelegramAPIError as e:
                    logger.warning(
                        "Failed to send material %s to user %s (attempt %d/%d): %s",
                        material.id,
                        user_id,
                        attempt + 1,
                        self.MAX_RETRIES,
                        e,
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAY)
                    else:
                        await delivery_repo.mark_failed(existing.id, str(e))
                        failed += 1
                except Exception as e:
                    logger.exception(
                        "Unexpected error sending material %s to user %s: %s",
                        material.id,
                        user_id,
                        e,
                    )
                    await delivery_repo.mark_failed(existing.id, str(e))
                    failed += 1
                    break

        logger.info(
            "Delivery completed for user %s, training %s: %d delivered, %d failed",
            user_id,
            training_id,
            delivered,
            failed,
        )
        return {"success": True, "delivered": delivered, "failed": failed}

    async def _send_material(self, user_id: int, material: TrainingMaterialModel) -> None:
        """Send a single material to a user."""
        if material.material_type == "text" and material.text_content:
            await self.bot.send_message(user_id, material.text_content)
        elif material.material_type == "photo" and material.telegram_file_id:
            await self.bot.send_photo(
                user_id,
                material.telegram_file_id,
                caption=material.caption,
            )
        elif material.material_type == "video" and material.telegram_file_id:
            await self.bot.send_video(
                user_id,
                material.telegram_file_id,
                caption=material.caption,
            )
        elif (
            material.material_type == "document"
            and material.telegram_file_id
            or material.material_type == "pdf"
            and material.telegram_file_id
        ):
            await self.bot.send_document(
                user_id,
                material.telegram_file_id,
                caption=material.caption,
            )
        elif material.material_type == "audio" and material.telegram_file_id:
            await self.bot.send_audio(
                user_id,
                material.telegram_file_id,
                caption=material.caption,
            )
        else:
            logger.warning(
                "Material %s has no content to send (type: %s)",
                material.id,
                material.material_type,
            )
