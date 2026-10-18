from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tgbot.db.models import OrderItemModel, OrderModel
from tgbot.db.repositories.base import BaseRepository


class OrderRepository(BaseRepository):
    async def get_by_id(self, order_id: int) -> OrderModel | None:
        result = await self.session.execute(select(OrderModel).where(OrderModel.id == order_id))
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
        amount: Decimal | float,
        currency: str = "RUB",
        customer_email: str | None = None,
        payment_id: str | None = None,
        idempotency_key: str | None = None,
        checkout_key: str | None = None,
        discount_percent: Decimal | float = Decimal("0.00"),
        discount_amount: Decimal | float = Decimal("0.00"),
        payment_metadata_version: str = "v2",
        items: list[dict] | None = None,
    ) -> OrderModel:
        order = OrderModel(
            user_id=user_id,
            training_id=training_id,
            amount=Decimal(str(amount)),
            discount_percent=Decimal(str(discount_percent)),
            discount_amount=Decimal(str(discount_amount)),
            currency=currency,
            status="pending",
            customer_email=customer_email,
            payment_id=payment_id,
            idempotency_key=idempotency_key,
            payment_metadata_version=payment_metadata_version,
            checkout_key=checkout_key,
        )
        self.session.add(order)
        try:
            await self.session.flush()
            for item in items or []:
                self.session.add(
                    OrderItemModel(
                        order_id=order.id,
                        training_id=item["training_id"],
                        item_price=Decimal(str(item["item_price"])),
                        is_discounted=item.get("is_discounted", False),
                    )
                )
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if checkout_key:
                existing = await self.get_pending_checkout(checkout_key)
                if existing:
                    return existing
            raise
        await self.session.refresh(order)
        return order

    async def get_pending_checkout(self, checkout_key: str) -> OrderModel | None:
        result = await self.session.execute(
            select(OrderModel)
            .where(
                OrderModel.checkout_key == checkout_key,
                OrderModel.status == "pending",
            )
            .order_by(OrderModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def set_payment_details(self, order_id: int, payment_id: str, payment_url: str) -> None:
        order = await self.get_by_id(order_id)
        if order:
            order.payment_id = payment_id
            order.payment_url = payment_url
            order.payment_metadata_version = "v2"
            await self.session.commit()

    async def get_order_items(self, order_id: int) -> list[OrderItemModel]:
        result = await self.session.execute(
            select(OrderItemModel)
            .where(OrderItemModel.order_id == order_id)
            .order_by(OrderItemModel.id)
        )
        return list(result.scalars().all())

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
