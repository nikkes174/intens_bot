"""Tests for admin panel."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import CreateChatInviteLink

from tgbot.config import Config


@pytest.fixture
def admin_config():
    return Config(
        tg_bot=type("TgBot", (), {"token": "test", "admin_ids": [123456789]})(),
        db=type("DbConfig", (), {"url": "sqlite+aiosqlite:///test.db"})(),
        yookassa=type("YooKassaConfig", (), {})(),
        webhook=type("WebhookConfig", (), {})(),
        misc=type("Miscellaneous", (), {"log_level": "INFO"})(),
    )


def test_admin_can_open_admin_panel(admin_config):
    """Test that admin can open admin panel."""
    from tgbot.handlers.admin import is_admin

    assert is_admin(123456789, admin_config) is True


def test_user_cannot_open_admin_panel(admin_config):
    """Test that regular user cannot open admin panel."""
    from tgbot.handlers.admin import is_admin

    assert is_admin(987654321, admin_config) is False


@pytest.mark.asyncio
async def test_training_title_changed(training_repo):
    """Test that training title can be changed."""
    training = await training_repo.create(
        title="Старое название",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )

    await training_repo.update_title(training.id, "Новое название")

    updated = await training_repo.get_by_id(training.id)
    assert updated.title == "Новое название"


@pytest.mark.asyncio
async def test_training_price_changed(training_repo):
    """Test that training price can be changed."""
    training = await training_repo.create(
        title="Тренировка",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )

    await training_repo.update_price(training.id, Decimal("1500.00"))

    updated = await training_repo.get_by_id(training.id)
    assert updated.price == Decimal("1500.00")


@pytest.mark.asyncio
async def test_material_added(material_repo, sample_training):
    """Test that material can be added."""
    material = await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="file_123",
        file_name="test.mp4",
        sort_order=1,
    )

    assert material.id is not None
    assert material.training_id == sample_training.id


@pytest.mark.asyncio
async def test_material_deleted(material_repo, sample_training):
    """Test that material can be deleted."""
    material = await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="file_123",
        file_name="test.mp4",
        sort_order=1,
    )

    deleted = await material_repo.delete(material.id)
    assert deleted is True

    fetched = await material_repo.get_by_id(material.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_material_order_changed(material_repo, sample_training):
    """Test that material order can be changed."""
    mat1 = await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="file_1",
        file_name="first.mp4",
        sort_order=1,
    )
    mat2 = await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="file_2",
        file_name="second.mp4",
        sort_order=2,
    )

    # Swap order
    await material_repo.update_sort_order(mat1.id, 2)
    await material_repo.update_sort_order(mat2.id, 1)

    materials = await material_repo.get_by_training(sample_training.id)
    assert materials[0].file_name == "second.mp4"
    assert materials[1].file_name == "first.mp4"


@pytest.mark.asyncio
async def test_group_linked_to_training(training_repo):
    """Test that group can be linked to training."""
    training = await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )

    await training_repo.update_chat_id(training.id, -1001234567890)

    updated = await training_repo.get_by_id(training.id)
    assert updated.telegram_chat_id == -1001234567890


@pytest.mark.asyncio
async def test_same_group_cannot_link_to_two_trainings(training_repo):
    """Test that same group cannot be linked to two trainings."""
    training1 = await training_repo.create(
        title="Тренировка №1",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=1,
    )
    await training_repo.create(
        title="Тренировка №2",
        description="Тест",
        price=Decimal("990.00"),
        sort_order=2,
    )

    await training_repo.update_chat_id(training1.id, -1001234567890)

    # Try to link same group to training2
    existing = await training_repo.get_by_chat_id(-1001234567890)
    assert existing is not None
    assert existing.id == training1.id


@pytest.mark.asyncio
async def test_regular_user_cannot_use_admin_price_state(admin_config):
    from tgbot.handlers.admin import process_new_price

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=987654321),
        text="1",
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock())

    await process_new_price(message, state, admin_config)

    state.clear.assert_awaited_once()
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_chatid_admin_in_group_checks_join_request_link_support(admin_config):
    from tgbot.handlers.admin import chat_id

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789),
        chat=SimpleNamespace(id=-1001234567890, type="supergroup"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()
    bot.id = 123
    bot.get_chat_member.return_value = SimpleNamespace(
        status="administrator", can_invite_users=True
    )
    bot.create_chat_invite_link.return_value = SimpleNamespace(
        invite_link="https://t.me/+temporary-probe"
    )

    await chat_id(message, bot, admin_config)

    assert "-1001234567890" in message.answer.await_args.args[0]
    bot.create_chat_invite_link.assert_awaited_once()
    assert bot.create_chat_invite_link.await_args.kwargs["creates_join_request"] is True
    bot.revoke_chat_invite_link.assert_awaited_once_with(
        chat_id=-1001234567890,
        invite_link="https://t.me/+temporary-probe",
    )


@pytest.mark.asyncio
async def test_chatid_does_not_reveal_group_id_to_regular_user(admin_config):
    from tgbot.handlers.admin import chat_id

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=987654321),
        chat=SimpleNamespace(id=-1001234567890, type="group"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()

    await chat_id(message, bot, admin_config)

    assert "-1001234567890" not in message.answer.await_args.args[0]
    bot.get_chat_member.assert_not_called()


@pytest.mark.asyncio
async def test_chatid_in_private_chat_explains_where_to_run_it(admin_config):
    from tgbot.handlers.admin import chat_id

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789),
        chat=SimpleNamespace(id=123456789, type="private"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()

    await chat_id(message, bot, admin_config)

    assert "внутри Telegram-группы" in message.answer.await_args.args[0]
    bot.get_chat_member.assert_not_called()


@pytest.mark.asyncio
async def test_chatid_rejects_bot_without_invite_rights(admin_config):
    from tgbot.handlers.admin import chat_id

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789),
        chat=SimpleNamespace(id=-1001234567890, type="group"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()
    bot.id = 123
    bot.get_chat_member.return_value = SimpleNamespace(
        status="administrator", can_invite_users=False
    )

    await chat_id(message, bot, admin_config)

    bot.create_chat_invite_link.assert_not_called()
    assert "права администратора" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_chatid_reports_join_request_link_api_failure(admin_config):
    from tgbot.handlers.admin import chat_id

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789),
        chat=SimpleNamespace(id=-1001234567890, type="supergroup"),
        answer=AsyncMock(),
    )
    bot = AsyncMock()
    bot.id = 123
    bot.get_chat_member.return_value = SimpleNamespace(
        status="administrator", can_invite_users=True
    )
    bot.create_chat_invite_link.side_effect = TelegramBadRequest(
        method=CreateChatInviteLink(
            chat_id=message.chat.id, name="probe", creates_join_request=True
        ),
        message="join requests are unavailable",
    )

    await chat_id(message, bot, admin_config)

    assert "-1001234567890" not in message.answer.await_args.args[0]
    assert "Не удалось проверить" in message.answer.await_args.args[0]
    bot.revoke_chat_invite_link.assert_not_called()
