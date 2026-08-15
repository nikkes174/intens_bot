from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.db.models import UserModel


class UserCrud:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_user(
        self,
        user_id: int,
        user_name: Optional[str] = None,
        create_date: Optional[date] = None,
    ):
        user = UserModel(
            user_id=user_id,
            user_name=user_name,
            create_date=create_date,
            payment_ok=False,
        )

        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def get_users_list(self):
        result = await self.session.execute(select(UserModel))
        return result.scalars().all()

    async def get_user(self, user_id: int):
        result = await self.session.execute(
            select(UserModel).where(UserModel.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def set_payment_pending(
        self, user_id: int, user_name: Optional[str], payment_id: str
    ) -> UserModel:
        user = await self.get_user(user_id)
        if user is None:
            user = UserModel(
                user_id=user_id,
                user_name=user_name,
                create_date=date.today(),
                payment_ok=False,
            )
            self.session.add(user)
        elif user_name:
            user.user_name = user_name

        user.payment_id = payment_id
        user.payment_ok = False
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def mark_payment_succeeded(self, user_id: int, payment_id: str) -> bool:
        user = await self.get_user(user_id)
        if user is None or user.payment_id != payment_id:
            return False

        user.payment_ok = True
        await self.session.commit()
        return True

    async def has_successful_payment(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        return bool(user and user.payment_ok)
