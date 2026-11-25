from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tgbot.db.analytics import AnalyticsService, normalize_source
from tgbot.db.db import Base
from tgbot.db.models import FunnelEventModel, TrafficSourceModel, UserVisitModel


def test_start_payload_normalization_is_dynamic_and_safe():
    assert normalize_source(" YouTube ") == "youtube"
    assert normalize_source("instagram-blackfriday") == "instagram-blackfriday"
    assert normalize_source("") == "organic"
    assert normalize_source("contains spaces") == "organic"
    assert normalize_source("a" * 65) == "organic"


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "analytics.db")
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_visits_are_history_and_last_touch_is_frozen(session):
    analytics = AnalyticsService(session)
    instagram = await analytics.register_visit(1, "instagram")
    youtube = await analytics.register_visit(1, "youtube")
    await analytics.register_visit(1, "youtube2")
    await analytics.register_visit(2, "youtube")

    assert instagram != youtube
    visits = (await session.execute(UserVisitModel.__table__.select())).all()
    assert len(visits) == 4

    payment = await analytics.create_payment_record(
        "payment-1", 1, Decimal("777.00"), visit_id=youtube
    )
    await analytics.register_visit(1, "organic")
    refreshed = await analytics.get_payment("payment-1")
    assert refreshed.source_id == payment.source_id
    source_name = await session.execute(
        TrafficSourceModel.__table__.select().where(
            TrafficSourceModel.id == refreshed.source_id
        )
    )
    assert source_name.one().name == "youtube"


@pytest.mark.asyncio
async def test_success_is_idempotent_canceled_is_not_revenue(session):
    analytics = AnalyticsService(session)
    visit = await analytics.register_visit(1, "youtube2")
    await analytics.create_payment_record(
        "paid", 1, Decimal("100.00"), visit_id=visit
    )
    await analytics.create_payment_record(
        "canceled", 1, Decimal("200.00"), visit_id=visit
    )

    assert await analytics.mark_payment_success("paid") is True
    assert await analytics.mark_payment_success("paid") is False
    assert await analytics.mark_payment_canceled("canceled") is True

    events = (
        await session.execute(
            FunnelEventModel.__table__.select().where(
                FunnelEventModel.payment_id == "paid",
                FunnelEventModel.event == "payment_succeeded",
            )
        )
    ).all()
    assert len(events) == 1

    stats = await analytics.get_funnel_statistics("youtube*")
    assert stats["users"] == 1
    assert stats["payments"] == 1
    assert stats["revenue"] == Decimal("100.00")
