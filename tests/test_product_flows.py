"""Integration tests for bundle entitlements, discounts, and durable follow-ups."""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramServerError
from aiogram.methods import SendMessage
from sqlalchemy import select

from tgbot.config import TrainingGroups, YooKassaConfig
from tgbot.db.db import get_session_factory
from tgbot.db.models import OutboxEventModel
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.seed import seed_products
from tgbot.handlers import user as user_handlers
from tgbot.handlers.user import buy_discount_offer, get_materials, join_group, my_purchases
from tgbot.services.delivery import DeliveryService
from tgbot.services.discounts import check_discount_eligibility
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


def verified_success(order, payment_id):
    return {
        "id": payment_id,
        "status": "succeeded",
        "amount": str(order.amount),
        "currency": order.currency,
        "metadata": {
            "user_id": str(order.user_id),
            "training_id": str(order.training_id),
            "order_id": str(order.id),
            "metadata_version": "2",
        },
    }


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
    payment_info = verified_success(order, "bundle-payment")

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
async def test_bundle_success_outbox_sends_all_materials_and_channel_invites(
    payment_service,
    user_repo,
    sample_user,
    training_repo,
    order_repo,
    access_repo,
    material_repo,
    outbox_repo,
):
    await seed_products()
    bundle = await training_repo.get_by_code("bundle_all")
    component_ids = await training_repo.get_component_ids(bundle.id)
    group_ids = (-1009000000001, -1009000000002, -1009000000003)
    for index, training_id in enumerate(component_ids, 1):
        # Stale DB IDs must not override the current .env mapping.
        await training_repo.update_chat_id(training_id, -1008000000000 - index)
        await material_repo.create(
            training_id=training_id,
            material_type="pdf",
            telegram_file_id=f"bundle-pdf-{index}",
            file_name=f"bundle-{index}.pdf",
            sort_order=1,
        )

    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=bundle.id,
        amount=Decimal("3290.00"),
        payment_id="bundle-full-flow",
        items=[{"training_id": bundle.id, "item_price": Decimal("3290.00")}],
    )
    bot = AsyncMock()
    bot.create_chat_invite_link.side_effect = [
        SimpleNamespace(invite_link=f"https://t.me/+bundle{index}") for index in range(1, 4)
    ]
    training_groups = TrainingGroups(
        glute_id=group_ids[0], v_share_id=group_ids[1], core_protocol_id=group_ids[2]
    )
    with patch.object(
        payment_service,
        "get_payment_status",
        return_value=verified_success(order, "bundle-full-flow"),
    ):
        assert await payment_service.process_payment_succeeded(
            "bundle-full-flow",
            bot=bot,
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=outbox_repo,
            user_repo=user_repo,
            training_repo=training_repo,
        )

    await OutboxProcessor(bot, training_groups=training_groups).process_events()

    assert bot.send_document.await_count == 3
    assert bot.create_chat_invite_link.await_count == 3
    assert [call.kwargs["chat_id"] for call in bot.create_chat_invite_link.await_args_list] == list(
        group_ids
    )
    assert all(
        call.kwargs["creates_join_request"] for call in bot.create_chat_invite_link.await_args_list
    )
    assert bot.send_message.await_count == 4
    assert {
        access.training_id for access in await access_repo.get_user_accesses(sample_user.id)
    } >= set(component_ids)
    await order_repo.session.refresh(order)
    assert order.delivery_status == "sent"


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
    payment_info = verified_success(order, "discount-payment")
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
async def test_bundle_materials_can_be_resent_for_all_three_programs(
    sample_user,
    training_repo,
    material_repo,
    delivery_repo,
    access_repo,
):
    await seed_products()
    bundle = await training_repo.get_by_code("bundle_all")
    component_ids = await training_repo.get_component_ids(bundle.id)
    bot = AsyncMock()
    delivery_service = DeliveryService(bot)

    await access_repo.grant_access(sample_user.id, bundle.id)
    for index, training_id in enumerate(component_ids, 1):
        await access_repo.grant_access(sample_user.id, training_id)
        await material_repo.create(
            training_id=training_id,
            material_type="pdf",
            telegram_file_id=f"pdf-{index}",
            file_name=f"program-{index}.pdf",
            sort_order=1,
        )
        await delivery_service.deliver_materials(
            sample_user.id,
            training_id,
            delivery_repo,
            material_repo,
            training_repo,
        )

    original_sent_at = {
        training_id: (
            await delivery_repo.get_delivery(
                sample_user.id,
                (await material_repo.get_by_training(training_id))[0].id,
            )
        ).sent_at
        for training_id in component_ids
    }
    callback = make_callback(sample_user.telegram_user_id, f"get_materials_{bundle.id}")

    await get_materials(callback, bot)

    assert bot.send_document.await_count == 6
    for training_id in component_ids:
        material = (await material_repo.get_by_training(training_id))[0]
        delivery = await delivery_repo.get_delivery(sample_user.id, material.id)
        assert delivery.status == "sent"
        assert delivery.sent_at == original_sent_at[training_id]


@pytest.mark.asyncio
async def test_bundle_owner_can_regenerate_all_three_channel_invites(
    sample_user, training_repo, access_repo
):
    await seed_products()
    bundle = await training_repo.get_by_code("bundle_all")
    component_ids = await training_repo.get_component_ids(bundle.id)
    for index, training_id in enumerate(component_ids, 1):
        await access_repo.grant_access(sample_user.id, training_id)
        await training_repo.update_chat_id(training_id, -1000000000000 - index)
    training_groups = TrainingGroups(
        glute_id=-1009100000001,
        v_share_id=-1009100000002,
        core_protocol_id=-1009100000003,
    )

    bot = AsyncMock()
    bot.create_chat_invite_link.side_effect = [
        SimpleNamespace(invite_link=f"https://t.me/+channel{index}") for index in range(1, 4)
    ]
    callback = make_callback(sample_user.telegram_user_id, f"join_group_{bundle.id}")

    await join_group(callback, bot, SimpleNamespace(training_groups=training_groups))

    assert bot.create_chat_invite_link.await_count == 3
    assert callback.message.answer.await_count == 3
    assert [call.kwargs["chat_id"] for call in bot.create_chat_invite_link.await_args_list] == [
        training_groups.glute_id,
        training_groups.v_share_id,
        training_groups.core_protocol_id,
    ]


@pytest.mark.asyncio
async def test_bundle_purchase_is_listed_as_three_individual_programs(
    sample_user, training_repo, access_repo
):
    await seed_products()
    bundle = await training_repo.get_by_code("bundle_all")
    await access_repo.grant_access(sample_user.id, bundle.id)
    for training_id in await training_repo.get_component_ids(bundle.id):
        await access_repo.grant_access(sample_user.id, training_id)

    callback = make_callback(sample_user.telegram_user_id, "my_purchases")
    await my_purchases(callback)

    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for button in buttons if button.callback_data]
    expected = {
        f"my_product_{(await training_repo.get_by_code(code)).id}"
        for code in ("glute", "vshape", "core")
    }
    assert set(callbacks[:-1]) == expected
    assert callbacks[-1] == "main_menu"


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
    scheduled_repo, sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=product.id,
        amount=product.price,
        payment_id="followup-payment",
    )
    paid_at = datetime.utcnow() - timedelta(days=7, seconds=1)
    await order_repo.update_status(order.id, "succeeded", paid_at=paid_at)
    await access_repo.grant_access(sample_user.id, product.id, order.id)
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
        duplicate = await ScheduledMessageRepository(new_session).schedule(
            dedupe_key=job.dedupe_key,
            user_id=sample_user.id,
            message_type="followup_72h",
            scheduled_at=future,
        )
        assert duplicate.id == job.id


@pytest.mark.asyncio
async def test_scheduled_job_can_be_claimed_by_only_one_worker(scheduled_repo, sample_user):
    job = await scheduled_repo.schedule(
        dedupe_key="one-worker-only",
        user_id=sample_user.id,
        message_type="followup_72h",
        scheduled_at=datetime.utcnow() - timedelta(seconds=1),
    )
    session_factory = get_session_factory()
    async with session_factory() as worker_a, session_factory() as worker_b:
        first = await ScheduledMessageRepository(worker_a).claim(job.id)
        second = await ScheduledMessageRepository(worker_b).claim(job.id)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_temporary_telegram_error_is_retried_and_saved(
    scheduled_repo, sample_user, training_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    due = datetime.utcnow() - timedelta(seconds=1)
    job = await scheduled_repo.schedule(
        dedupe_key="temporary-telegram-failure",
        user_id=sample_user.id,
        training_id=product.id,
        message_type="card_view_24h",
        scheduled_at=due,
    )
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramServerError(
        method=SendMessage(chat_id=sample_user.telegram_user_id, text="test"),
        message="temporary server error",
    )

    await ScheduledMessageProcessor(bot).process_due(now=datetime.utcnow())

    await scheduled_repo.session.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 1
    assert "temporary server error" in job.last_error
    assert job.scheduled_at > due


@pytest.mark.asyncio
async def test_blocked_bot_is_not_retried(scheduled_repo, sample_user, training_repo):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    job = await scheduled_repo.schedule(
        dedupe_key="blocked-bot",
        user_id=sample_user.id,
        training_id=product.id,
        message_type="card_view_24h",
        scheduled_at=datetime.utcnow() - timedelta(seconds=1),
    )
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(
        method=SendMessage(chat_id=sample_user.telegram_user_id, text="test"),
        message="bot was blocked",
    )

    await ScheduledMessageProcessor(bot).process_due(now=datetime.utcnow())

    await scheduled_repo.session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1
    assert "bot was blocked" in job.last_error


@pytest.mark.asyncio
async def test_uncertain_send_is_not_retried_after_recording_failure(
    monkeypatch, scheduled_repo, sample_user, training_repo
):
    await seed_products()
    product = await training_repo.get_by_code("glute")
    job = await scheduled_repo.schedule(
        dedupe_key="send-before-db-record",
        user_id=sample_user.id,
        training_id=product.id,
        message_type="card_view_24h",
        scheduled_at=datetime.utcnow() - timedelta(seconds=1),
    )
    bot = AsyncMock()

    async def fail_mark_sent(self, job_id, now=None):
        raise RuntimeError("database commit failed after send")

    monkeypatch.setattr(ScheduledMessageRepository, "mark_sent", fail_mark_sent)
    processor = ScheduledMessageProcessor(bot)
    await processor.process_due(now=datetime.utcnow())
    await processor.process_due(now=datetime.utcnow())

    await scheduled_repo.session.refresh(job)
    assert bot.send_message.await_count == 1
    assert job.status == "failed"
    assert "Telegram accepted the send" in job.last_error


@pytest.mark.asyncio
async def test_stale_processing_job_is_terminal_after_restart(scheduled_repo, sample_user):
    job = await scheduled_repo.schedule(
        dedupe_key="stale-processing",
        user_id=sample_user.id,
        message_type="followup_72h",
        scheduled_at=datetime.utcnow() - timedelta(seconds=1),
    )
    assert await scheduled_repo.claim(job.id)

    recovered = await scheduled_repo.recover_processing(
        stale_before=datetime.utcnow() + timedelta(seconds=1)
    )

    await scheduled_repo.session.refresh(job)
    assert recovered == 1
    assert job.status == "failed"
    assert "outcome is uncertain" in job.last_error


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
    await order_repo.update_status(
        order.id, "succeeded", paid_at=datetime.utcnow() - timedelta(days=7, seconds=1)
    )
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


async def _record_individual_purchase(
    *, user, product, order_repo, access_repo, paid_at, payment_suffix
):
    order = await order_repo.create(
        user_id=user.id,
        training_id=product.id,
        amount=product.price,
        payment_id=f"discount-eligibility-{payment_suffix}",
        items=[{"training_id": product.id, "item_price": product.price}],
    )
    await order_repo.update_status(order.id, "succeeded", paid_at=paid_at)
    await access_repo.grant_access(user.id, product.id, order.id)
    return order


@pytest.mark.asyncio
async def test_discount_is_unavailable_without_an_individual_purchase(
    sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    result = await check_discount_eligibility(
        sample_user.id,
        access_repo=access_repo,
        order_repo=order_repo,
        training_repo=training_repo,
    )
    assert not result.available
    assert result.reason == "no_individual_purchase"
    assert len(result.missing_products) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("age_days", [0, 6])
async def test_discount_waits_at_least_seven_days(
    age_days, sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    now = datetime.utcnow()
    glute = await training_repo.get_by_code("glute")
    await _record_individual_purchase(
        user=sample_user,
        product=glute,
        order_repo=order_repo,
        access_repo=access_repo,
        paid_at=now - timedelta(days=age_days),
        payment_suffix=str(age_days),
    )
    result = await check_discount_eligibility(
        sample_user.id,
        access_repo=access_repo,
        order_repo=order_repo,
        training_repo=training_repo,
        now=now,
    )
    assert not result.available
    assert result.reason == "purchase_too_recent"


@pytest.mark.asyncio
async def test_discount_after_seven_days_only_prices_unowned_programs(
    sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    now = datetime.utcnow()
    glute = await training_repo.get_by_code("glute")
    vshape = await training_repo.get_by_code("vshape")
    core = await training_repo.get_by_code("core")
    await _record_individual_purchase(
        user=sample_user,
        product=glute,
        order_repo=order_repo,
        access_repo=access_repo,
        paid_at=now - timedelta(days=7),
        payment_suffix="seven-days",
    )

    result = await check_discount_eligibility(
        sample_user.id,
        access_repo=access_repo,
        order_repo=order_repo,
        training_repo=training_repo,
        now=now,
    )

    assert result.available
    assert [product.product_code for product in result.missing_products] == ["vshape", "core"]
    assert result.total == Decimal("1904.00")
    assert result.discount_amount == Decimal("476.00")
    assert {line["training_id"] for line in result.lines} == {vshape.id, core.id}


@pytest.mark.asyncio
async def test_discount_is_unavailable_after_all_programs_are_owned(
    sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    now = datetime.utcnow()
    for code in ("glute", "vshape", "core"):
        product = await training_repo.get_by_code(code)
        await _record_individual_purchase(
            user=sample_user,
            product=product,
            order_repo=order_repo,
            access_repo=access_repo,
            paid_at=now - timedelta(days=8),
            payment_suffix=code,
        )

    result = await check_discount_eligibility(
        sample_user.id,
        access_repo=access_repo,
        order_repo=order_repo,
        training_repo=training_repo,
        now=now,
    )
    assert not result.available
    assert result.reason == "all_programs_owned"
    assert result.total == Decimal("0.00")


@pytest.mark.asyncio
async def test_bundle_purchase_does_not_create_discount_eligibility(
    sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    bundle = await training_repo.get_by_code("bundle_all")
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=bundle.id,
        amount=Decimal("3290.00"),
        payment_id="discount-bundle-purchase",
        items=[{"training_id": bundle.id, "item_price": Decimal("3290.00")}],
    )
    await order_repo.update_status(
        order.id, "succeeded", paid_at=datetime.utcnow() - timedelta(days=8)
    )
    for training_id in await training_repo.get_component_ids(bundle.id):
        await access_repo.grant_access(sample_user.id, training_id, order.id)

    result = await check_discount_eligibility(
        sample_user.id,
        access_repo=access_repo,
        order_repo=order_repo,
        training_repo=training_repo,
    )
    assert not result.available
    assert result.reason == "no_individual_purchase"


@pytest.mark.asyncio
async def test_forged_discount_callback_cannot_bypass_eligibility(
    sample_user, training_repo, order_repo, access_repo
):
    await seed_products()
    callback = make_callback(sample_user.telegram_user_id, "buy_discount_offer")
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())

    await buy_discount_offer(callback, state)

    callback.message.edit_text.assert_awaited_once()
    state.set_state.assert_not_awaited()
    assert await order_repo.get_user_orders(sample_user.id) == []


@pytest.mark.asyncio
async def test_old_pending_discount_order_does_not_bypass_eligibility(
    sample_user, training_repo, order_repo
):
    await seed_products()
    products = [await training_repo.get_by_code(code) for code in ("glute", "vshape", "core")]
    ids = ",".join(str(product.id) for product in products)
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=products[0].id,
        amount=Decimal("3096.00"),
        checkout_key=f"user:{sample_user.id}:discount:{ids}",
        payment_id="old-discount-pending",
        items=[
            {"training_id": product.id, "item_price": Decimal("0.00"), "is_discounted": True}
            for product in products
        ],
        discount_percent=Decimal("20.00"),
    )
    await order_repo.set_payment_details(
        order.id, "old-discount-pending", "https://pay.example/old"
    )

    result = await user_handlers._create_or_resume_payment(
        telegram_id=sample_user.telegram_user_id,
        username=sample_user.username,
        email="test@example.com",
        selection={"kind": "discount"},
        config=SimpleNamespace(yookassa=YooKassaConfig(shop_id="shop", secret_key="secret")),
    )

    assert result[0] is None
    assert len(await order_repo.get_user_orders(sample_user.id)) == 1
