
from sqlalchemy import select

from tgbot.db.models import UserModel
from tgbot.db.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_telegram_id(self, telegram_user_id: int) -> UserModel | None:
        result = await self.session.execute(
            select(UserModel).where(UserModel.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> UserModel | None:
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, telegram_user_id: int, username: str | None = None) -> UserModel:
        user = UserModel(
            telegram_user_id=telegram_user_id,
            username=username,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def get_or_create(self, telegram_user_id: int, username: str | None = None) -> UserModel:
        user = await self.get_by_telegram_id(telegram_user_id)
        if user is None:
            user = await self.create(telegram_user_id, username)
        elif username and user.username != username:
            user.username = username
            await self.session.commit()
        return user

    async def update_email(self, user_id: int, email: str) -> None:
        user = await self.get_by_id(user_id)
        if user:
            user.email = email
            await self.session.commit()

    async def get_all(self) -> list[UserModel]:
        result = await self.session.execute(select(UserModel))
        return list(result.scalars().all())
