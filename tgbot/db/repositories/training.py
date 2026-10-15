from sqlalchemy import select

from tgbot.db.models import TrainingModel
from tgbot.db.repositories.base import BaseRepository


class TrainingRepository(BaseRepository):
    async def get_by_id(self, training_id: int) -> TrainingModel | None:
        result = await self.session.execute(
            select(TrainingModel).where(TrainingModel.id == training_id)
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> list[TrainingModel]:
        result = await self.session.execute(
            select(TrainingModel).where(TrainingModel.is_active).order_by(TrainingModel.sort_order)
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[TrainingModel]:
        result = await self.session.execute(
            select(TrainingModel).order_by(TrainingModel.sort_order)
        )
        return list(result.scalars().all())

    async def create(
        self,
        title: str,
        description: str | None = None,
        price: float = 0,
        sort_order: int = 0,
    ) -> TrainingModel:
        training = TrainingModel(
            title=title,
            description=description,
            price=price,
            sort_order=sort_order,
        )
        self.session.add(training)
        await self.session.commit()
        await self.session.refresh(training)
        return training

    async def update_title(self, training_id: int, title: str) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.title = title
            await self.session.commit()

    async def update_description(self, training_id: int, description: str) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.description = description
            await self.session.commit()

    async def update_price(self, training_id: int, price: float) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.price = price
            await self.session.commit()

    async def update_chat_id(self, training_id: int, chat_id: int) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.telegram_chat_id = chat_id
            await self.session.commit()

    async def set_active(self, training_id: int, is_active: bool) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.is_active = is_active
            await self.session.commit()

    async def get_by_chat_id(self, chat_id: int) -> TrainingModel | None:
        result = await self.session.execute(
            select(TrainingModel).where(TrainingModel.telegram_chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def count_materials(self, training_id: int) -> int:
        from sqlalchemy import func

        from tgbot.db.models import TrainingMaterialModel

        result = await self.session.execute(
            select(func.count(TrainingMaterialModel.id)).where(
                TrainingMaterialModel.training_id == training_id
            )
        )
        return result.scalar_one()
