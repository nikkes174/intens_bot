import asyncio
import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from yookassa import Configuration, Payment
from yookassa.domain.exceptions import NotFoundError

from tgbot.config import YooKassaConfig
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, config: YooKassaConfig):
        self.config = config
        self._configured = False

    def _configure(self) -> None:
        if self._configured:
            return
        if not self.config.shop_id or not self.config.secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
        Configuration.account_id = self.config.shop_id
        Configuration.secret_key = self.config.secret_key
        self._configured = True

    async def create_payment(
        self,
        user_id: int,
        training_id: int,
        email: str,
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
    ) -> tuple[str, str]:
        """Create a YooKassa payment and return (payment_id, confirmation_url)."""
        self._configure()

        idempotency_key = str(uuid.uuid4())

        payload = {
            "amount": {"value": str(amount), "currency": currency},
            "confirmation": {
                "type": "redirect",
                "return_url": self.config.return_url or "https://t.me/",
            },
            "capture": True,
            "description": description or f"Покупка тренировки #{training_id}",
            "metadata": {
                "user_id": str(user_id),
                "training_id": str(training_id),
                "idempotency_key": idempotency_key,
            },
            "receipt": {
                "customer": {"email": email},
                "items": [{
                    "description": description or f"Тренировка #{training_id}",
                    "quantity": "1.00",
                    "amount": {"value": str(amount), "currency": currency},
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                    "vat_code": self.config.vat_code,
                }],
            },
        }

        payment = await asyncio.to_thread(Payment.create, payload, idempotency_key)

        return payment.id, payment.confirmation.confirmation_url

    async def get_payment_status(self, payment_id: str) -> dict | None:
        """Fetch payment status from YooKassa API."""
        self._configure()
        try:
            payment = await asyncio.to_thread(Payment.find_one, payment_id)
            return {
                "id": payment.id,
                "status": payment.status,
                "amount": payment.amount.value,
                "currency": payment.amount.currency,
                "metadata": payment.metadata or {},
            }
        except NotFoundError:
            logger.warning("Payment %s not found in YooKassa", payment_id)
            return None
        except Exception as e:
            logger.error("Failed to fetch payment %s: %s", payment_id, e)
            return None

    async def process_payment_succeeded(
        self,
        payment_id: str,
        bot: Bot,
        order_repo: OrderRepository,
        access_repo: AccessRepository,
        outbox_repo: OutboxRepository,
        user_repo: UserRepository,
        training_repo: TrainingRepository,
    ) -> bool:
        """Process a successful payment webhook. Returns True if processed."""
        order = await order_repo.get_by_payment_id(payment_id)
        if not order:
            logger.warning("Order for payment %s not found", payment_id)
            return False

        if order.status == "succeeded":
            logger.info("Order %s already succeeded, skipping", order.id)
            return True

        # Verify with YooKassa API
        payment_info = await self.get_payment_status(payment_id)
        if not payment_info:
            logger.error("Could not verify payment %s with YooKassa", payment_id)
            return False

        if payment_info["status"] != "succeeded":
            logger.warning(
                "Payment %s status is %s, not succeeded",
                payment_id,
                payment_info["status"],
            )
            return False

        # Verify amount and currency
        if Decimal(payment_info["amount"]) != order.amount:
            logger.error(
                "Amount mismatch for payment %s: expected %s, got %s",
                payment_id, order.amount, payment_info["amount"],
            )
            return False

        if payment_info["currency"] != order.currency:
            logger.error(
                "Currency mismatch for payment %s: expected %s, got %s",
                payment_id, order.currency, payment_info["currency"],
            )
            return False

        # Update order status
        await order_repo.update_status(
            order.id, "succeeded", paid_at=datetime.utcnow()
        )

        # Grant access
        await access_repo.grant_access(order.user_id, order.training_id, order.id)

        # Create outbox events for delivery and invite
        await outbox_repo.create_event(
            "payment_succeeded",
            json.dumps({
                "order_id": order.id,
                "user_id": order.user_id,
                "training_id": order.training_id,
                "payment_id": payment_id,
            }),
        )

        logger.info("Payment %s processed successfully for order %s", payment_id, order.id)
        return True

    async def process_payment_canceled(
        self,
        payment_id: str,
        order_repo: OrderRepository,
    ) -> bool:
        """Process a canceled payment webhook."""
        order = await order_repo.get_by_payment_id(payment_id)
        if not order:
            logger.warning("Order for payment %s not found", payment_id)
            return False

        if order.status == "succeeded":
            logger.info("Order %s already succeeded, ignoring cancel", order.id)
            return True

        await order_repo.update_status(order.id, "canceled")
        logger.info("Payment %s canceled for order %s", payment_id, order.id)
        return True
