import asyncio
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tgbot.db.db import Base, get_engine, get_session_factory, init_db, close_db
from tgbot.db.repositories.user import UserRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.outbox import OutboxRepository
from tgbot.db.repositories.invite import InviteRepository


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    db_path = "test.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    db_config = type("Config", (), {"url": f"sqlite+aiosqlite:///{db_path}"})()
    get_engine(db_config)
    await init_db(db_config)

    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session

    await close_db()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture
async def user_repo(db_session):
    return UserRepository(db_session)


@pytest_asyncio.fixture
async def training_repo(db_session):
    return TrainingRepository(db_session)


@pytest_asyncio.fixture
async def material_repo(db_session):
    return MaterialRepository(db_session)


@pytest_asyncio.fixture
async def order_repo(db_session):
    return OrderRepository(db_session)


@pytest_asyncio.fixture
async def access_repo(db_session):
    return AccessRepository(db_session)


@pytest_asyncio.fixture
async def delivery_repo(db_session):
    return DeliveryRepository(db_session)


@pytest_asyncio.fixture
async def outbox_repo(db_session):
    return OutboxRepository(db_session)


@pytest_asyncio.fixture
async def invite_repo(db_session):
    return InviteRepository(db_session)


@pytest_asyncio.fixture
async def sample_user(user_repo):
    return await user_repo.create(telegram_user_id=123456789, username="testuser")


@pytest_asyncio.fixture
async def sample_training(training_repo):
    return await training_repo.create(
        title="Тренировка №1",
        description="Тестовая тренировка",
        price=Decimal("990.00"),
        sort_order=1,
    )


@pytest_asyncio.fixture
async def sample_material(material_repo, sample_training):
    return await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="file_123",
        file_name="test.mp4",
        sort_order=1,
    )
