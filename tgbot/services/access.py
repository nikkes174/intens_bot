import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.db.repositories.training import TrainingRepository

logger = logging.getLogger(__name__)


class AccessService:
    INVITE_LINK_EXPIRE_HOURS = 24

    def __init__(self, bot: Bot):
        self.bot = bot

    async def check_access(
        self,
        user_id: int,
        training_id: int,
        access_repo: AccessRepository,
    ) -> bool:
        """Check if user has active access to a training."""
        return await access_repo.has_active_access(user_id, training_id)

    async def create_invite_link(
        self,
        user_id: int,
        training_id: int,
        training_repo: TrainingRepository,
        invite_repo: InviteRepository,
    ) -> str | None:
        """Create an invite link for a user to join the training group."""
        training = await training_repo.get_by_id(training_id)
        if not training or not training.telegram_chat_id:
            logger.warning("Training %s has no group configured", training_id)
            return None

        try:
            expire_date = datetime.utcnow() + timedelta(hours=self.INVITE_LINK_EXPIRE_HOURS)
            invite = await self.bot.create_chat_invite_link(
                chat_id=training.telegram_chat_id,
                name=f"user-{user_id}-training-{training_id}",
                creates_join_request=True,
                expire_date=expire_date,
            )

            await invite_repo.create(
                training_id=training_id,
                user_id=user_id,
                invite_link=invite.invite_link,
                chat_id=training.telegram_chat_id,
                creates_join_request=True,
                expire_date=expire_date,
            )

            return invite.invite_link
        except TelegramAPIError as e:
            logger.error("Failed to create invite link for user %s: %s", user_id, e)
            return None

    async def handle_join_request(
        self,
        user_id: int,
        chat_id: int,
        access_repo: AccessRepository,
        training_repo: TrainingRepository,
    ) -> bool:
        """Handle a chat join request. Returns True if approved."""
        training = await training_repo.get_by_chat_id(chat_id)
        if not training:
            logger.warning("No training found for chat %s", chat_id)
            return False

        has_access = await access_repo.has_active_access(user_id, training.id)
        if has_access:
            logger.info(
                "Approving join request from user %s for training %s",
                user_id, training.id,
            )
            return True

        logger.info(
            "Ignoring join request from user %s for training %s (no access)",
            user_id, training.id,
        )
        return False

    async def approve_join_request(
        self,
        user_id: int,
        chat_id: int,
        access_repo: AccessRepository,
        training_repo: TrainingRepository,
    ) -> bool:
        """Approve a join request if user has access."""
        training = await training_repo.get_by_chat_id(chat_id)
        if not training:
            return False

        has_access = await access_repo.has_active_access(user_id, training.id)
        return bool(has_access)
