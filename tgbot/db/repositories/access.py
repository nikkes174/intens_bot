from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tgbot.db.models import UserTrainingAccessModel
from tgbot.db.repositories.base import BaseRepository


class AccessRepository(BaseRepository):
    async def get_access(self, user_id: int, training_id: int) -> UserTrainingAccessModel | None:
        result = await self.session.execute(
            select(UserTrainingAccessModel).where(
                UserTrainingAccessModel.user_id == user_id,
                UserTrainingAccessModel.training_id == training_id,
            )
        )
        return result.scalar_one_or_none()

    async def has_active_access(self, user_id: int, training_id: int) -> bool:
        access = await self.get_access(user_id, training_id)
        return bool(access and access.is_active)

    async def grant_access(
        self,
        user_id: int,
        training_id: int,
        order_id: int | None = None,
    ) -> UserTrainingAccessModel:
        access = await self.get_access(user_id, training_id)
        if access:
            if access.is_active:
                return access
            access.is_active = True
            access.order_id = order_id
            access.granted_at = datetime.utcnow()
            access.revoked_at = None
        else:
            access = UserTrainingAccessModel(
                user_id=user_id,
                training_id=training_id,
                order_id=order_id,
                is_active=True,
            )
            self.session.add(access)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            access = await self.get_access(user_id, training_id)
            if access:
                return access
            raise
        await self.session.refresh(access)
        return access

    async def revoke_access(self, user_id: int, training_id: int) -> None:
        access = await self.get_access(user_id, training_id)
        if access:
            access.is_active = False
            access.revoked_at = datetime.utcnow()
            await self.session.commit()

    async def get_user_accesses(self, user_id: int) -> list[UserTrainingAccessModel]:
        result = await self.session.execute(
            select(UserTrainingAccessModel).where(
                UserTrainingAccessModel.user_id == user_id,
                UserTrainingAccessModel.is_active,
            )
        )
        return list(result.scalars().all())
