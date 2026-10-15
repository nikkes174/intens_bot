import json
import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tgbot.config import YooKassaConfig
from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.services.payment import PaymentService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/yookassa")
async def yookassa_webhook(request: Request, config: YooKassaConfig):
    """Handle YooKassa webhook notifications."""
    try:
        body = await request.body()
        data = json.loads(body)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Invalid webhook payload: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    event = data.get("event")
    payment_data = data.get("object", {})

    if not event or not payment_data:
        logger.error("Missing event or object in webhook payload")
        raise HTTPException(status_code=400, detail="Missing event or object")

    payment_id = payment_data.get("id")
    if not payment_id:
        logger.error("Missing payment_id in webhook payload")
        raise HTTPException(status_code=400, detail="Missing payment_id")

    logger.info("Received YooKassa webhook: event=%s, payment_id=%s", event, payment_id)

    session_factory = get_session_factory()

    if event == "payment.succeeded":
        async with session_factory() as session:
            order_repo = OrderRepository(session)
            access_repo = AccessRepository(session)
            outbox_repo = OutboxRepository(session)

            order = await order_repo.get_by_payment_id(payment_id)
            if not order:
                logger.warning("Order for payment %s not found", payment_id)
                return JSONResponse({"status": "ok"})

            if order.status == "succeeded":
                logger.info("Order %s already succeeded", order.id)
                return JSONResponse({"status": "ok"})

            # Verify with YooKassa API
            payment_service = PaymentService(config)
            payment_info = await payment_service.get_payment_status(payment_id)

            if not payment_info or payment_info["status"] != "succeeded":
                logger.error("Payment %s verification failed", payment_id)
                raise HTTPException(status_code=400, detail="Payment verification failed")

            if Decimal(payment_info["amount"]) != order.amount:
                logger.error("Amount mismatch for payment %s", payment_id)
                raise HTTPException(status_code=400, detail="Amount mismatch")

            if payment_info["currency"] != order.currency:
                logger.error("Currency mismatch for payment %s", payment_id)
                raise HTTPException(status_code=400, detail="Currency mismatch")

            # Update order
            await order_repo.update_status(order.id, "succeeded", paid_at=datetime.utcnow())

            # Grant access
            await access_repo.grant_access(order.user_id, order.training_id, order.id)

            # Create outbox event
            await outbox_repo.create_event(
                "payment_succeeded",
                json.dumps(
                    {
                        "order_id": order.id,
                        "user_id": order.user_id,
                        "training_id": order.training_id,
                        "payment_id": payment_id,
                    }
                ),
            )

            logger.info("Payment %s processed successfully", payment_id)

    elif event == "payment.canceled":
        async with session_factory() as session:
            order_repo = OrderRepository(session)
            order = await order_repo.get_by_payment_id(payment_id)
            if order and order.status != "succeeded":
                await order_repo.update_status(order.id, "canceled")
                logger.info("Payment %s canceled", payment_id)

    return JSONResponse({"status": "ok"})
