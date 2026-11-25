import asyncio
import sys
import types
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# PaymentService imports YooKassa at module import time.  The tests replace it
# with a local stub when the optional production dependency is not installed;
# no network payment API is ever called.
if "yookassa" not in sys.modules:
    yookassa_stub = types.ModuleType("yookassa")

    class Configuration:
        account_id = None
        secret_key = None

    class Payment:
        pass

    yookassa_stub.Configuration = Configuration
    yookassa_stub.Payment = Payment
    sys.modules["yookassa"] = yookassa_stub

from tgbot.db.analytics import AnalyticsService
from tgbot.db.db import Base
from tgbot.db.models import (
    FunnelEventModel,
    PaymentAnalyticsModel,
    PaymentInviteModel,
    UserModel,
)
from tgbot.db.crud_users import UserCrud
from tgbot.services import payment as payment_module
from tgbot.services.payment import PaymentService


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr(payment_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@dataclass
class _Invite:
    invite_link: str


class _Bot:
    def __init__(self):
        self.create_calls = 0
        self.sent = []
        self.fail_send = False

    async def create_chat_invite_link(self, **kwargs):
        self.create_calls += 1
        return _Invite("https://t.me/+invite-{}".format(self.create_calls))

    async def send_message(self, user_id, text, **kwargs):
        if self.fail_send:
            raise RuntimeError("simulated Telegram delivery failure")
        self.sent.append((user_id, text, kwargs))


class _JoinRequest:
    def __init__(self, user_id, invite_link):
        self.from_user = types.SimpleNamespace(id=user_id)
        self.invite_link = _Invite(invite_link)
        self.approved = False
        self.declined = False

    async def approve(self):
        self.approved = True

    async def decline(self):
        self.declined = True


def _configure_service(service):
    service.channel_id = "-100123"
    return service


@pytest.mark.asyncio
async def test_concurrent_invite_delivery_is_serialized():
    service = _configure_service(PaymentService())
    active = 0
    maximum_active = 0

    async def implementation(_user_id, _bot, _payment_id):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    service._send_join_request_link_impl = implementation

    await asyncio.gather(
        service._send_join_request_link(7, _Bot(), "payment-1"),
        service._send_join_request_link(7, _Bot(), "payment-1"),
    )

    assert maximum_active == 1
    assert service._invite_locks == {}


@pytest.mark.asyncio
async def test_failed_delivery_reuses_saved_invite(session_factory):
    service = _configure_service(PaymentService())
    bot = _Bot()
    bot.fail_send = True

    with pytest.raises(RuntimeError):
        await service._send_join_request_link(7, bot, "payment-2")

    bot.fail_send = False
    await service._send_join_request_link(7, bot, "payment-2")

    assert bot.create_calls == 1
    assert len(bot.sent) == 1
    async with session_factory() as session:
        invite = await session.scalar(
            select(PaymentInviteModel).where(
                PaymentInviteModel.payment_id == "payment-2"
            )
        )
        assert invite.invite_link == "https://t.me/+invite-1"


@pytest.mark.asyncio
async def test_recovery_delivers_successful_payment_once(session_factory):
    service = _configure_service(PaymentService())
    bot = _Bot()

    async with session_factory() as session:
        analytics = AnalyticsService(session)
        payment = await analytics.create_payment_record(
            "payment-3", 8, 100, status="pending"
        )
        assert await analytics.mark_payment_success(payment.payment_id)
        await UserCrud(session).set_payment_pending(8, "user", "payment-3")

    await service.recover_pending_payments(bot)
    await service.recover_pending_payments(bot)

    assert len(bot.sent) == 1
    async with session_factory() as session:
        user = await session.scalar(
            select(UserModel).where(UserModel.user_id == 8)
        )
        event = await session.scalar(
            select(FunnelEventModel).where(
                FunnelEventModel.event == "access_link_sent",
                FunnelEventModel.payment_id == "payment-3",
            )
        )
        assert user.payment_ok is True
        assert event is not None


@pytest.mark.asyncio
async def test_access_is_attributed_to_invite_payment_not_latest_payment(
    session_factory,
):
    service = _configure_service(PaymentService())
    bot = _Bot()

    async with session_factory() as session:
        user = UserModel(
            user_id=9,
            user_name="user",
            payment_id="payment-new",
            payment_ok=True,
        )
        session.add(user)
        await session.commit()

        analytics = AnalyticsService(session)
        visit = await analytics.register_visit(9, "promo")
        first = await analytics.create_payment_record(
            "payment-old", 9, 100, visit_id=visit
        )
        latest = await analytics.create_payment_record(
            "payment-new", 9, 100, visit_id=visit
        )
        assert await analytics.mark_payment_success(first.payment_id)
        assert await analytics.mark_payment_success(latest.payment_id)
        session.add(
            PaymentInviteModel(
                payment_id="payment-old",
                user_id=9,
                invite_link="https://t.me/+old",
            )
        )
        await session.commit()

    join_request = _JoinRequest(9, "https://t.me/+old")
    assert await service.approve_join_request(join_request, bot) is True
    assert join_request.approved is True

    async with session_factory() as session:
        event = await session.scalar(
            select(FunnelEventModel).where(
                FunnelEventModel.event == "access_granted",
                FunnelEventModel.user_id == 9,
            )
        )
        assert event.payment_id == "payment-old"


@pytest.mark.asyncio
async def test_recovery_loop_survives_one_unexpected_error():
    service = PaymentService()
    service.recovery_interval = 0
    calls = []

    async def recover(_bot):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("unexpected recovery failure")
        raise asyncio.CancelledError

    service.recover_pending_payments = recover
    task = asyncio.create_task(service._recovery_loop(_Bot()))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert len(calls) == 2
