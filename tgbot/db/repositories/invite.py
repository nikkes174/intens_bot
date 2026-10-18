from datetime import datetime

from sqlalchemy import select

from tgbot.db.models import InviteLinkModel
from tgbot.db.repositories.base import BaseRepository


class InviteRepository(BaseRepository):
    async def create(
        self,
        training_id: int,
        user_id: int,
        invite_link: str,
        chat_id: int,
        creates_join_request: bool = True,
        expire_date: datetime | None = None,
    ) -> InviteLinkModel:
        invite = InviteLinkModel(
            training_id=training_id,
            user_id=user_id,
            invite_link=invite_link,
            chat_id=chat_id,
            creates_join_request=creates_join_request,
            expire_date=expire_date,
        )
        self.session.add(invite)
        await self.session.commit()
        await self.session.refresh(invite)
        return invite

    async def get_active_for_user(self, user_id: int, training_id: int) -> InviteLinkModel | None:
        now = datetime.utcnow()
        result = await self.session.execute(
            select(InviteLinkModel)
            .where(
                InviteLinkModel.user_id == user_id,
                InviteLinkModel.training_id == training_id,
                InviteLinkModel.is_active,
                (InviteLinkModel.expire_date.is_(None)) | (InviteLinkModel.expire_date > now),
            )
            .order_by(InviteLinkModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
