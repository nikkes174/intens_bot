from sqlalchemy import select

from tgbot.db.models import TrainingMaterialModel
from tgbot.db.repositories.base import BaseRepository


class MaterialRepository(BaseRepository):
    async def get_by_id(self, material_id: int) -> TrainingMaterialModel | None:
        result = await self.session.execute(
            select(TrainingMaterialModel).where(TrainingMaterialModel.id == material_id)
        )
        return result.scalar_one_or_none()

    async def get_by_training(self, training_id: int) -> list[TrainingMaterialModel]:
        result = await self.session.execute(
            select(TrainingMaterialModel)
            .where(TrainingMaterialModel.training_id == training_id)
            .order_by(TrainingMaterialModel.sort_order)
        )
        return list(result.scalars().all())

    async def create(
        self,
        training_id: int,
        material_type: str,
        telegram_file_id: str | None = None,
        telegram_file_unique_id: str | None = None,
        file_name: str | None = None,
        caption: str | None = None,
        text_content: str | None = None,
        sort_order: int = 0,
    ) -> TrainingMaterialModel:
        material = TrainingMaterialModel(
            training_id=training_id,
            material_type=material_type,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            file_name=file_name,
            caption=caption,
            text_content=text_content,
            sort_order=sort_order,
        )
        self.session.add(material)
        await self.session.commit()
        await self.session.refresh(material)
        return material

    async def delete(self, material_id: int) -> bool:
        material = await self.get_by_id(material_id)
        if material:
            await self.session.delete(material)
            await self.session.commit()
            return True
        return False

    async def update_sort_order(self, material_id: int, sort_order: int) -> None:
        material = await self.get_by_id(material_id)
        if material:
            material.sort_order = sort_order
            await self.session.commit()

    async def get_max_sort_order(self, training_id: int) -> int:
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.max(TrainingMaterialModel.sort_order)).where(
                TrainingMaterialModel.training_id == training_id
            )
        )
        return result.scalar_one() or 0
