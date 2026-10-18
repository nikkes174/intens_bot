import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from yookassa import Configuration, Payment
from yookassa.domain.exceptions import NotFoundError

from tgbot.config import YooKassaConfig
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository

logger = logging.getLogger(__name__)

# YooKassa requires a VAT code on receipt items. Code 1 means "без НДС" and is
# used for this self-employed merchant setup; it does not need an env setting.
SELF_EMPLOYED_VAT_CODE = 1


def _metadata_to_dict(metadata) -> dict:
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return {str(key): value for key, value in metadata.items()}
    if hasattr(metadata, "to_dict"):
        converted = metadata.to_dict()
        if isinstance(converted, dict):
            return {str(key): value for key, value in converted.items()}
    try:
        return {
            str(key): value for key, value in vars(metadata).items() if not str(key).startswith("_")
        }
    except TypeError:
        return {}


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
        order_id: int,
        email: str,
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
        idempotency_key: str | None = None,
        receipt_items: list[dict] | None = None,
    ) -> tuple[str, str]:
        """Create a YooKassa payment and return (payment_id, confirmation_url)."""
        self._configure()

        idempotency_key = idempotency_key or str(uuid.uuid4())

        line_items = receipt_items or [
            {
                "description": description or f"Тренировка #{training_id}",
                "amount": Decimal(amount),
            }
        ]

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
                "order_id": str(order_id),
                "metadata_version": "2",
                "idempotency_key": idempotency_key,
            },
            "receipt": {
                "customer": {"email": email},
                "items": [
                    {
                        "description": item["description"][:128],
                        "quantity": "1.00",
                        "amount": {
                            "value": str(Decimal(item["amount"]).quantize(Decimal("0.01"))),
                            "currency": currency,
                        },
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                        "vat_code": SELF_EMPLOYED_VAT_CODE,
                    }
                    for item in line_items
                ],
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
                "metadata": _metadata_to_dict(payment.metadata),
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
        scheduled_repo: ScheduledMessageRepository | None = None,
    ) -> bool:
        """Process a successful payment webhook. Returns True if processed."""
        order = await order_repo.get_by_payment_id(payment_id)
        if not order:
            logger.warning("Order for payment %s not found", payment_id)
            return False

        already_succeeded = order.status == "succeeded"
        # Webhooks are notifications only: always verify authoritative payment data.
        payment_info = await self.get_payment_status(payment_id)
        if not payment_info or payment_info.get("status") != "succeeded":
            logger.warning("Payment %s is not confirmed as succeeded", payment_id)
            return False

        if not self._verified_payment_matches_order(order, payment_id, payment_info):
            return False

        order_items = await order_repo.get_order_items(order.id)
        purchased_ids = [item.training_id for item in order_items] or [order.training_id]
        grant_ids: set[int] = set()
        for purchased_id in purchased_ids:
            grant_ids.update(await training_repo.get_component_ids(purchased_id))
            # Record the bundle itself as owned too; its component rights remain separate.
            product = await training_repo.get_by_id(purchased_id)
            if product and product.is_bundle:
                await access_repo.grant_access(order.user_id, purchased_id, order.id)
        for training_id in sorted(grant_ids):
            await access_repo.grant_access(order.user_id, training_id, order.id)

        paid_at = order.paid_at or datetime.utcnow()
        if not already_succeeded:
            await order_repo.update_status(order.id, "succeeded", paid_at=paid_at)

        payload = json.dumps(
            {
                "order_id": order.id,
                "user_id": order.user_id,
                "training_id": order.training_id,
                "training_ids": sorted(grant_ids),
                "product_ids": purchased_ids,
                "payment_id": payment_id,
            }
        )
        await outbox_repo.create_event(
            "payment_succeeded", payload, dedupe_key=f"payment:{order.id}:succeeded"
        )

        if scheduled_repo:
            for training_id in sorted(grant_ids):
                await scheduled_repo.cancel_product_reminders(order.user_id, [training_id])
            await scheduled_repo.cancel_product_reminders(order.user_id, purchased_ids)

            if not await scheduled_repo.get_by_dedupe_key(f"order:{order.id}:followup-72h"):
                await scheduled_repo.schedule(
                    dedupe_key=f"order:{order.id}:followup-72h",
                    user_id=order.user_id,
                    order_id=order.id,
                    message_type="followup_72h",
                    scheduled_at=paid_at + timedelta(hours=72),
                )

            if order.discount_percent == Decimal("0.00") and len(purchased_ids) == 1:
                product = await training_repo.get_by_id(purchased_ids[0])
                if product and not product.is_bundle:
                    await scheduled_repo.schedule(
                        dedupe_key=f"order:{order.id}:followup-7d",
                        user_id=order.user_id,
                        order_id=order.id,
                        message_type="followup_7d",
                        scheduled_at=paid_at + timedelta(days=7),
                    )

        logger.info("Payment %s processed successfully for order %s", payment_id, order.id)
        return True

    @staticmethod
    def _verified_payment_matches_order(order, payment_id: str, payment_info: dict) -> bool:
        if payment_info.get("id") != payment_id or order.payment_id != payment_id:
            logger.error("Payment id mismatch for order %s", order.id)
            return False

        try:
            amount = Decimal(str(payment_info.get("amount"))).quantize(Decimal("0.01"))
        except Exception:
            logger.error("Invalid amount returned by YooKassa for order %s", order.id)
            return False
        if amount != order.amount:
            logger.error("Amount mismatch for payment %s and order %s", payment_id, order.id)
            return False
        if payment_info.get("currency") != order.currency:
            logger.error("Currency mismatch for payment %s and order %s", payment_id, order.id)
            return False

        metadata = _metadata_to_dict(payment_info.get("metadata"))
        expected = {
            "user_id": str(order.user_id),
            "training_id": str(order.training_id),
        }
        if order.payment_metadata_version == "v2":
            expected.update(
                {
                    "order_id": str(order.id),
                    "metadata_version": "2",
                }
            )
        elif metadata.get("order_id") is not None:
            # Legacy orders are accepted only by their persisted payment_id and the
            # metadata fields that the old payment creator already supplied.
            expected["order_id"] = str(order.id)

        if order.idempotency_key:
            expected["idempotency_key"] = order.idempotency_key

        mismatched = [
            field for field, value in expected.items() if str(metadata.get(field, "")) != value
        ]
        if mismatched:
            logger.error(
                "YooKassa metadata mismatch for payment %s/order %s: %s",
                payment_id,
                order.id,
                ", ".join(mismatched),
            )
            return False
        return True

    async def process_payment_canceled(
        self,
        payment_id: str,
        order_repo: OrderRepository,
        outbox_repo: OutboxRepository | None = None,
        training_repo: TrainingRepository | None = None,
    ) -> bool:
        """Process a canceled payment webhook."""
        order = await order_repo.get_by_payment_id(payment_id)
        if not order:
            logger.warning("Order for payment %s not found", payment_id)
            return False

        if order.status == "succeeded":
            logger.info("Order %s already succeeded, ignoring cancel", order.id)
            return True

        if order.status != "canceled":
            payment_info = await self.get_payment_status(payment_id)
            if not payment_info or payment_info["status"] != "canceled":
                logger.info("Payment %s is not confirmed as canceled", payment_id)
                return False
            await order_repo.update_status(order.id, "canceled")

        if outbox_repo:
            product_code = None
            if order.discount_percent > Decimal("0"):
                product_code = "discount_offer"
            elif training_repo:
                product = await training_repo.get_by_id(order.training_id)
                product_code = product.product_code if product else None
            await outbox_repo.create_event(
                "payment_canceled",
                json.dumps(
                    {
                        "order_id": order.id,
                        "user_id": order.user_id,
                        "product_code": product_code,
                    }
                ),
                dedupe_key=f"payment:{order.id}:canceled",
            )
        logger.info("Payment %s canceled for order %s", payment_id, order.id)
        return True
