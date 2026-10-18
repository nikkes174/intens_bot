from decimal import Decimal

from sqlalchemy import select

from tgbot.db.models import TrainingModel
from tgbot.db.repositories.base import BaseRepository


class TrainingRepository(BaseRepository):
    async def get_by_id(self, training_id: int) -> TrainingModel | None:
        result = await self.session.execute(
            select(TrainingModel).where(TrainingModel.id == training_id)
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, product_code: str) -> TrainingModel | None:
        result = await self.session.execute(
            select(TrainingModel).where(TrainingModel.product_code == product_code)
        )
        return result.scalar_one_or_none()

    async def get_products(self, active_only: bool = True) -> list[TrainingModel]:
        query = select(TrainingModel).where(TrainingModel.product_code.is_not(None))
        if active_only:
            query = query.where(TrainingModel.is_active)
        result = await self.session.execute(query.order_by(TrainingModel.sort_order))
        return list(result.scalars().all())

    async def get_bundle_components(self, bundle_id: int) -> list[TrainingModel]:
        from tgbot.db.models import ProductComponentModel

        result = await self.session.execute(
            select(TrainingModel)
            .join(ProductComponentModel, ProductComponentModel.training_id == TrainingModel.id)
            .where(ProductComponentModel.bundle_id == bundle_id)
            .order_by(TrainingModel.sort_order)
        )
        return list(result.scalars().all())

    async def get_component_ids(self, training_id: int) -> list[int]:
        from tgbot.db.models import ProductComponentModel

        training = await self.get_by_id(training_id)
        if not training or not training.is_bundle:
            return [training_id] if training else []
        result = await self.session.execute(
            select(ProductComponentModel.training_id)
            .where(ProductComponentModel.bundle_id == training_id)
            .order_by(ProductComponentModel.id)
        )
        return list(result.scalars().all())

    async def add_bundle_component(self, bundle_id: int, training_id: int) -> None:
        from sqlalchemy.exc import IntegrityError

        from tgbot.db.models import ProductComponentModel

        exists = await self.session.execute(
            select(ProductComponentModel.id).where(
                ProductComponentModel.bundle_id == bundle_id,
                ProductComponentModel.training_id == training_id,
            )
        )
        if exists.scalar_one_or_none() is not None:
            return
        self.session.add(ProductComponentModel(bundle_id=bundle_id, training_id=training_id))
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()

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
        product_code: str | None = None,
        is_bundle: bool = False,
    ) -> TrainingModel:
        training = TrainingModel(
            product_code=product_code,
            title=title,
            description=description,
            price=price,
            sort_order=sort_order,
            is_bundle=is_bundle,
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

    async def update_price(self, training_id: int, price: Decimal | float) -> None:
        training = await self.get_by_id(training_id)
        if training:
            training.price = Decimal(str(price))
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
