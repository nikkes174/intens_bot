"""Integration tests for bundle entitlements, discounts, and durable follow-ups."""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from tgbot.config import YooKassaConfig
from tgbot.db.db import get_session_factory
from tgbot.db.models import OutboxEventModel
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.seed import seed_products
from tgbot.handlers import user as user_handlers
from tgbot.handlers.user import get_materials, my_purchases
from tgbot.services.outbox import OutboxProcessor
from tgbot.services.payment import PaymentService
from tgbot.services.pricing import discounted_items
from tgbot.services.scheduled import ScheduledMessageProcessor


def make_callback(telegram_id: int, data: str):
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(
        from_user=SimpleNamespace(id=telegram_id, username="tester"),
        data=data,
        message=message,
        answer=AsyncMock(),
    )


@pytest.fixture
def payment_service():
    return PaymentService(
        YooKassaConfig(
            shop_id="test_shop",
            secret_key="test_secret",
            return_url="https://t.me/test_bot",
        )
    )


@pytest.mark.asyncio
async def test_bundle_payment_grants_all_three_programs_once(
    payment_service,
    user_repo,
    sample_user,
    training_repo,
    order_repo,
    access_repo,
    outbox_repo,
    scheduled_repo,
):
    await seed_products()
    products = {item.product_code: item for item in await training_repo.get_products()}
    bundle = products["bundle_all"]
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=bundle.id,
        amount=Decimal("3290.00"),
        payment_id="bundle-payment",
        items=[{"training_id": bundle.id, "item_price": Decimal("3290.00")}],
    )
    payment_info = {
        "id": "bundle-payment",
        "status": "succeeded",
        "amount": "3290.00",
        "currency": "RUB",
    }

    with patch.object(payment_service, "get_payment_status", return_value=payment_info):
        assert await payment_service.process_payment_succeeded(
            "bundle-payment",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=outbox_repo,
            user_repo=user_repo,
            training_repo=training_repo,
            scheduled_repo=scheduled_repo,
        )
        # A duplicate webhook completes idempotently and reuses the same outbox row.
        assert await payment_service.process_payment_succeeded(
            "bundle-payment",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=outbox_repo,
            user_repo=user_repo,
            training_repo=training_repo,
            scheduled_repo=scheduled_repo,
        )

    owned_ids = {
        access.training_id for access in await access_repo.get_user_accesses(sample_user.id)
    }
    assert owned_ids == {products[code].id for code in ("glute", "vshape", "core", "bundle_all")}
    result = await order_repo.session.execute(
        select(OutboxEventModel).where(
            OutboxEventModel.dedupe_key == f"payment:{order.id}:succeeded"
        )
    )
    assert len(result.scalars().all()) == 1
    assert await scheduled_repo.get_by_dedupe_key(f"order:{order.id}:followup-72h")
    assert await scheduled_repo.get_by_dedupe_key(f"order:{order.id}:followup-7d") is None


@pytest.mark.asyncio
async def test_discount_is_rounded_and_grants_only_order_lines(
    payment_service,
    user_repo,
    sample_user,
    training_repo,
    order_repo,
    access_repo,
    outbox_repo,
):
    await seed_products()
    vshape = await training_repo.get_by_code("vshape")
    core = await training_repo.get_by_code("core")
    total, discount, lines = discounted_items(
        [
            (vshape.id, vshape.title, Decimal("1290.00")),
            (core.id, core.title, Decimal("1090.00")),
        ]
    )
    assert total == Decimal("1904.00")
    assert discount == Decimal("476.00")
    assert sum((line["item_price"] for line in lines), Decimal("0.00")) == total

    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=vshape.id,
        amount=total,
        discount_percent=Decimal("20.00"),
        discount_amount=discount,
        payment_id="discount-payment",
        items=[
            {
                "training_id": line["training_id"],
                "item_price": line["item_price"],
                "is_discounted": True,
            }
            for line in lines
        ],
    )
    payment_info = {
        "id": "discount-payment",
        "status": "succeeded",
        "amount": "1904.00",
        "currency": "RUB",
    }
    with patch.object(payment_service, "get_payment_status", return_value=payment_info):
        assert await payment_service.process_payment_succeeded(
            "discount-payment",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=outbox_repo,
            user_repo=user_repo,
            training_repo=training_repo,
        )

    assert {a.training_id for a in await access_repo.get_user_accesses(sample_user.id)} == {
        vshape.id,
        core.id,
    }
    saved = await order_repo.get_by_id(order.id)
    assert saved.discount_percent == Decimal("20.00")
    assert saved.discount_amount == Decimal("476.00")


@pytest.mark.asyncio
async def test_user_cannot_get_another_users_materials(
    user_repo, access_repo, training_repo, sample_user
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    another_user = await user_repo.create(987654321, "buyer")
    await access_repo.grant_access(another_user.id, product.id)

    callback = make_callback(sample_user.telegram_user_id, f"get_materials_{product.id}")
    bot = AsyncMock()
    await get_materials(callback, bot)

    bot.send_document.assert_not_called()
    bot.send_video.assert_not_called()
    assert "нет доступа" in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_my_purchases_only_shows_products_owned_by_telegram_user(
    training_repo, access_repo, sample_user
):
    await seed_products()
    products = {item.product_code: item for item in await training_repo.get_products()}
    await access_repo.grant_access(sample_user.id, products["glute"].id)

    callback = make_callback(sample_user.telegram_user_id, "my_purchases")
    await my_purchases(callback)

    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for button in buttons if button.callback_data]
    assert callbacks == [f"my_product_{products['glute'].id}", "main_menu"]


@pytest.mark.asyncio
async def test_card_reminder_is_skipped_after_purchase(
    scheduled_repo, sample_user, training_repo, access_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    due = datetime.utcnow() - timedelta(seconds=1)
    job = await scheduled_repo.schedule(
        dedupe_key=f"view:{sample_user.id}:{product.id}",
        user_id=sample_user.id,
        training_id=product.id,
        message_type="card_view_24h",
        scheduled_at=due,
    )
    await access_repo.grant_access(sample_user.id, product.id)
    processor = ScheduledMessageProcessor(AsyncMock())

    await processor.process_due(now=datetime.utcnow())

    await scheduled_repo.session.refresh(job)
    assert job.status == "skipped"
    processor.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_one_card_reminder_per_user_per_24_hours(scheduled_repo, sample_user, training_repo):
    await seed_products()
    products = await training_repo.get_products()
    due = datetime.utcnow() - timedelta(seconds=1)
    for product in products[:2]:
        await scheduled_repo.schedule(
            dedupe_key=f"view:{sample_user.id}:{product.id}",
            user_id=sample_user.id,
            training_id=product.id,
            message_type="card_view_24h",
            scheduled_at=due,
        )
    bot = AsyncMock()
    await ScheduledMessageProcessor(bot).process_due(now=datetime.utcnow())

    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_purchase_followups_send_once_and_survive_new_processor(
    scheduled_repo, sample_user, training_repo, order_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=product.id,
        amount=product.price,
        payment_id="followup-payment",
    )
    await order_repo.update_status(order.id, "succeeded", paid_at=datetime.utcnow())
    due = datetime.utcnow() - timedelta(seconds=1)
    for message_type in ("followup_72h", "followup_7d"):
        await scheduled_repo.schedule(
            dedupe_key=f"followup:{order.id}:{message_type}",
            user_id=sample_user.id,
            order_id=order.id,
            message_type=message_type,
            scheduled_at=due,
        )

    bot = AsyncMock()
    processor = ScheduledMessageProcessor(bot)
    await processor.process_due(now=datetime.utcnow())
    await ScheduledMessageProcessor(bot).process_due(now=datetime.utcnow())

    assert bot.send_message.await_count == 2
    messages = [call.args[1] for call in bot.send_message.await_args_list]
    assert any("Как проходят первые тренировки?" in message for message in messages)
    assert any("скидкой 20%" in message for message in messages)


@pytest.mark.asyncio
async def test_scheduled_jobs_are_persistent_between_sessions(scheduled_repo, sample_user):
    future = datetime.utcnow() + timedelta(hours=72)
    job = await scheduled_repo.schedule(
        dedupe_key="persisted:three-day-followup",
        user_id=sample_user.id,
        message_type="followup_72h",
        scheduled_at=future,
    )

    session_factory = get_session_factory()
    async with session_factory() as new_session:
        recovered = await ScheduledMessageRepository(new_session).get_by_dedupe_key(job.dedupe_key)
        assert recovered is not None
        assert recovered.scheduled_at == future
        assert recovered.status == "pending"


@pytest.mark.asyncio
async def test_duplicate_checkout_key_reuses_existing_pending_order(
    order_repo, sample_user, sample_training
):
    user_id = sample_user.id
    first = await order_repo.create(
        user_id=user_id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        checkout_key=f"user:{sample_user.id}:product:test",
        idempotency_key="stable-idempotency-key",
    )
    second = await order_repo.create(
        user_id=user_id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        checkout_key=f"user:{sample_user.id}:product:test",
        idempotency_key="another-idempotency-key",
    )

    assert first.id == second.id
    assert len(await order_repo.get_user_orders(user_id)) == 1


@pytest.mark.asyncio
async def test_checkout_reuses_current_payment_link(
    monkeypatch, user_repo, training_repo, order_repo
):
    await seed_products()
    telegram_id = 555123
    user = await user_repo.create(telegram_id, "checkout_user")
    calls = []

    async def fake_create_payment(self, **kwargs):
        calls.append(kwargs)
        return "payment-one", "https://pay.example/one"

    monkeypatch.setattr(user_handlers.PaymentService, "create_payment", fake_create_payment)
    config = SimpleNamespace(yookassa=YooKassaConfig(shop_id="shop", secret_key="secret"))
    selection = {"kind": "product", "code": "glute"}
    args = {
        "telegram_id": telegram_id,
        "username": "checkout_user",
        "email": "checkout@example.com",
        "selection": selection,
        "config": config,
    }

    first = await user_handlers._create_or_resume_payment(**args)
    second = await user_handlers._create_or_resume_payment(**args)

    assert first[0] == second[0] == "https://pay.example/one"
    assert len(calls) == 1
    assert len(await order_repo.get_user_orders(user.id)) == 1


@pytest.mark.asyncio
async def test_seven_day_offer_is_skipped_when_all_programs_are_owned(
    scheduled_repo, sample_user, training_repo, access_repo, order_repo
):
    await seed_products()
    products = [await training_repo.get_by_code(code) for code in ("glute", "vshape", "core")]
    for product in products:
        await access_repo.grant_access(sample_user.id, product.id)
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=products[0].id,
        amount=products[0].price,
        payment_id="all-owned-followup",
    )
    await order_repo.update_status(order.id, "succeeded")
    job = await scheduled_repo.schedule(
        dedupe_key="all-owned:followup-7d",
        user_id=sample_user.id,
        order_id=order.id,
        message_type="followup_7d",
        scheduled_at=datetime.utcnow() - timedelta(seconds=1),
    )
    bot = AsyncMock()

    await ScheduledMessageProcessor(bot).process_due(now=datetime.utcnow())

    await scheduled_repo.session.refresh(job)
    assert job.status == "skipped"
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_payment_message_does_not_claim_missing_materials(
    sample_user, training_repo, order_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=product.id,
        amount=product.price,
        payment_id="without-materials",
    )
    bot = AsyncMock()
    await OutboxProcessor(bot)._handle_payment_succeeded(
        {
            "user_id": sample_user.id,
            "order_id": order.id,
            "training_ids": [product.id],
            "product_ids": [product.id],
        }
    )

    success_text = bot.send_message.await_args.args[1]
    assert "Материалы или приглашение" in success_text
    assert "файл прикреплён ниже" not in success_text
    await order_repo.session.refresh(order)
    assert order.delivery_status == "attention"
