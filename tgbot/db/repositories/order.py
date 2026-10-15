from datetime import datetime

from sqlalchemy import select

from tgbot.db.models import OrderModel
from tgbot.db.repositories.base import BaseRepository


class OrderRepository(BaseRepository):
    async def get_by_id(self, order_id: int) -> OrderModel | None:
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_payment_id(self, payment_id: str) -> OrderModel | None:
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.payment_id == payment_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        training_id: int,
        amount: float,
        currency: str = "RUB",
        customer_email: str | None = None,
        payment_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OrderModel:
        order = OrderModel(
            user_id=user_id,
            training_id=training_id,
            amount=amount,
            currency=currency,
            status="pending",
            customer_email=customer_email,
            payment_id=payment_id,
            idempotency_key=idempotency_key,
        )
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order

    async def update_status(
        self,
        order_id: int,
        status: str,
        paid_at: datetime | None = None,
    ) -> None:
        order = await self.get_by_id(order_id)
        if order:
            order.status = status
            if paid_at:
                order.paid_at = paid_at
            await self.session.commit()

    async def get_user_orders(self, user_id: int) -> list[OrderModel]:
        result = await self.session.execute(
            select(OrderModel)
            .where(OrderModel.user_id == user_id)
            .order_by(OrderModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_succeeded_orders(self) -> list[OrderModel]:
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.status == "succeeded")
        )
        return list(result.scalars().all())

    async def get_canceled_orders(self) -> list[OrderModel]:
        result = await self.session.execute(
            select(OrderModel).where(OrderModel.status == "canceled")
        )
        return list(result.scalars().all())

    async def get_total_revenue(self) -> float:
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.sum(OrderModel.amount)).where(OrderModel.status == "succeeded")
        )
        return result.scalar_one() or 0

    async def get_sales_by_training(self) -> list[tuple[int, int, float]]:
        from sqlalchemy import func
        result = await self.session.execute(
            select(
                OrderModel.training_id,
                func.count(OrderModel.id),
                func.sum(OrderModel.amount),
            )
            .where(OrderModel.status == "succeeded")
            .group_by(OrderModel.training_id)
        )
        return [(row[0], row[1], row[2] or 0) for row in result.all()]
