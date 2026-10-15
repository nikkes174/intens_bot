from datetime import datetime

from sqlalchemy import select

from tgbot.db.models import MaterialDeliveryModel
from tgbot.db.repositories.base import BaseRepository


class DeliveryRepository(BaseRepository):
    async def get_delivery(
        self, user_id: int, material_id: int
    ) -> MaterialDeliveryModel | None:
        result = await self.session.execute(
            select(MaterialDeliveryModel).where(
                MaterialDeliveryModel.user_id == user_id,
                MaterialDeliveryModel.material_id == material_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_pending_deliveries(
        self, user_id: int, training_id: int
    ) -> list[MaterialDeliveryModel]:
        result = await self.session.execute(
            select(MaterialDeliveryModel)
            .where(
                MaterialDeliveryModel.user_id == user_id,
                MaterialDeliveryModel.training_id == training_id,
                MaterialDeliveryModel.status != "sent",
            )
            .order_by(MaterialDeliveryModel.id)
        )
        return list(result.scalars().all())

    async def get_all_pending(self) -> list[MaterialDeliveryModel]:
        result = await self.session.execute(
            select(MaterialDeliveryModel).where(MaterialDeliveryModel.status != "sent")
        )
        return list(result.scalars().all())

    async def create(
        self,
        user_id: int,
        training_id: int,
        material_id: int,
    ) -> MaterialDeliveryModel:
        delivery = MaterialDeliveryModel(
            user_id=user_id,
            training_id=training_id,
            material_id=material_id,
            status="pending",
        )
        self.session.add(delivery)
        await self.session.commit()
        await self.session.refresh(delivery)
        return delivery

    async def mark_sent(self, delivery_id: int) -> None:
        delivery = await self.session.get(MaterialDeliveryModel, delivery_id)
        if delivery:
            delivery.status = "sent"
            delivery.sent_at = datetime.utcnow()
            await self.session.commit()

    async def mark_failed(self, delivery_id: int, error: str) -> None:
        delivery = await self.session.get(MaterialDeliveryModel, delivery_id)
        if delivery:
            delivery.status = "failed"
            delivery.attempts += 1
            delivery.last_error = error
            await self.session.commit()

    async def get_delivery_status(self, user_id: int, training_id: int) -> dict:
        result = await self.session.execute(
            select(MaterialDeliveryModel).where(
                MaterialDeliveryModel.user_id == user_id,
                MaterialDeliveryModel.training_id == training_id,
            )
        )
        deliveries = list(result.scalars().all())
        total = len(deliveries)
        sent = sum(1 for d in deliveries if d.status == "sent")
        return {"total": total, "sent": sent, "pending": total - sent}
