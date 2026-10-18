import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from tgbot.config import BUNDLE_PRODUCT_CODE, CORE_PRODUCT_CODES, TrainingGroups
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.db.repositories.training import TrainingRepository

logger = logging.getLogger(__name__)


class AccessService:
    INVITE_LINK_EXPIRE_HOURS = 24

    def __init__(self, bot: Bot, training_groups: TrainingGroups | None = None):
        self.bot = bot
        self.training_groups = training_groups or TrainingGroups()

    def get_training_chat_id(self, training) -> int | None:
        """Resolve primary product groups from config, retaining legacy DB groups only."""
        if not training or training.product_code == BUNDLE_PRODUCT_CODE:
            return None
        if training.product_code in CORE_PRODUCT_CODES:
            return self.training_groups.get_training_chat_id(training.product_code)
        return training.telegram_chat_id

    async def _training_for_chat_id(self, chat_id: int, training_repo: TrainingRepository):
        product_code = self.training_groups.get_training_product_code(chat_id)
        if product_code:
            return await training_repo.get_by_code(product_code)
        if self.training_groups.is_configured_chat_id(chat_id):
            return None

        # Keep old, non-catalog training links working, but never let a DB value
        # override the .env mapping for one of the three current protocols.
        training = await training_repo.get_by_chat_id(chat_id)
        if not training or training.product_code in CORE_PRODUCT_CODES | {BUNDLE_PRODUCT_CODE}:
            return None
        return training

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
        force_new: bool = False,
    ) -> str | None:
        """Create an invite link for a user to join the training group."""
        training = await training_repo.get_by_id(training_id)
        chat_id = self.get_training_chat_id(training)
        if not training or chat_id is None:
            logger.warning("Training %s has no valid group configured", training_id)
            return None

        if not force_new:
            active_invite = await invite_repo.get_active_for_user(user_id, training_id)
            if active_invite and isinstance(active_invite.invite_link, str):
                if active_invite.chat_id == chat_id:
                    return active_invite.invite_link
                try:
                    await self.bot.revoke_chat_invite_link(
                        chat_id=active_invite.chat_id,
                        invite_link=active_invite.invite_link,
                    )
                except TelegramAPIError:
                    logger.warning("Could not revoke stale invite for training %s", training_id)

        try:
            expire_date = datetime.utcnow() + timedelta(hours=self.INVITE_LINK_EXPIRE_HOURS)
            invite = await self.bot.create_chat_invite_link(
                chat_id=chat_id,
                name=f"user-{user_id}-training-{training_id}",
                creates_join_request=True,
                expire_date=expire_date,
            )

            await invite_repo.create(
                training_id=training_id,
                user_id=user_id,
                invite_link=invite.invite_link,
                chat_id=chat_id,
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
        training = await self._training_for_chat_id(chat_id, training_repo)
        if not training:
            logger.warning("No training found for chat %s", chat_id)
            return False

        has_access = await access_repo.has_active_access(user_id, training.id)
        if has_access:
            logger.info(
                "Approving join request from user %s for training %s",
                user_id,
                training.id,
            )
            return True

        logger.info(
            "Ignoring join request from user %s for training %s (no access)",
            user_id,
            training.id,
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
        training = await self._training_for_chat_id(chat_id, training_repo)
        if not training:
            return False

        has_access = await access_repo.has_active_access(user_id, training.id)
        return bool(has_access)


async def validate_training_groups(bot: Bot, groups: TrainingGroups) -> list[str]:
    """Probe configured groups independently; one broken group cannot stop startup."""
    problems = []
    for product_code in sorted(CORE_PRODUCT_CODES):
        chat_id = groups.get_training_chat_id(product_code)
        if chat_id is None:
            continue
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
            is_admin = member.status in {"administrator", "creator"}
            can_invite = member.status == "creator" or bool(
                getattr(member, "can_invite_users", False)
            )
            if not is_admin or not can_invite:
                raise RuntimeError("bot must be an administrator with invite permission")

            expire_date = datetime.utcnow() + timedelta(minutes=1)
            probe = await bot.create_chat_invite_link(
                chat_id=chat_id,
                name=f"startup-check-{product_code}",
                creates_join_request=True,
                expire_date=expire_date,
            )
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=probe.invite_link)
        except Exception as error:
            message = f"{product_code} ({chat_id}): {error}"
            logger.error("Training group configuration check failed: %s", message)
            problems.append(message)
        else:
            logger.info("Training group %s (%s) is ready", product_code, chat_id)
    return problems
