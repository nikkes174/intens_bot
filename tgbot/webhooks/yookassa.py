import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from tgbot.config import Config, YooKassaConfig
from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.services.payment import PaymentService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_yookassa_config(request: Request) -> YooKassaConfig:
    config: Config = request.app.state.config
    return config.yookassa


@router.post("/webhooks/yookassa")
async def yookassa_webhook(
    request: Request,
    config: Annotated[YooKassaConfig, Depends(_get_yookassa_config)],
):
    """Verify webhook notifications against YooKassa before changing access."""
    try:
        data = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        logger.warning("Invalid YooKassa webhook JSON: %s", error)
        raise HTTPException(status_code=400, detail="Invalid JSON") from error

    event = data.get("event")
    payment_data = data.get("object") or {}
    payment_id = payment_data.get("id")
    if not event or not payment_id:
        raise HTTPException(status_code=400, detail="Missing event or payment id")

    session_factory = get_session_factory()
    async with session_factory() as session:
        order_repo = OrderRepository(session)
        order = await order_repo.get_by_payment_id(payment_id)
        if not order:
            logger.warning("Order for payment %s not found", payment_id)
            return JSONResponse({"status": "ok"})

        payment_service = PaymentService(config)
        outbox_repo = OutboxRepository(session)
        training_repo = TrainingRepository(session)

        if event == "payment.succeeded":
            processed = await payment_service.process_payment_succeeded(
                payment_id=payment_id,
                bot=None,
                order_repo=order_repo,
                access_repo=AccessRepository(session),
                outbox_repo=outbox_repo,
                user_repo=UserRepository(session),
                training_repo=training_repo,
                scheduled_repo=ScheduledMessageRepository(session),
            )
            if not processed:
                # YooKassa can retry this notification; no access is granted until verified.
                raise HTTPException(status_code=400, detail="Payment verification failed")

        elif event == "payment.canceled":
            await payment_service.process_payment_canceled(
                payment_id,
                order_repo,
                outbox_repo=outbox_repo,
                training_repo=training_repo,
            )

    return JSONResponse({"status": "ok"})
