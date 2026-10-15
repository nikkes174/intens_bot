"""Tests for private group access."""

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError

from tgbot.services.access import AccessService


@pytest.fixture
def mock_bot():
    return AsyncMock()


@pytest.fixture
def access_service(mock_bot):
    return AccessService(mock_bot)


@pytest.mark.asyncio
async def test_paid_user_approved(
    access_service, access_repo, training_repo, sample_user, sample_training
):
    """Test that paid user is approved."""
    # Set up training with chat_id
    await training_repo.update_chat_id(sample_training.id, -1001234567890)
    await access_repo.grant_access(sample_user.id, sample_training.id)

    result = await access_service.handle_join_request(
        sample_user.id,
        -1001234567890,
        access_repo,
        training_repo,
    )

    assert result is True


@pytest.mark.asyncio
async def test_unpaid_user_not_approved(
    access_service, access_repo, training_repo, sample_user, sample_training
):
    """Test that unpaid user is not approved."""
    result = await access_service.handle_join_request(
        sample_user.id,
        sample_training.telegram_chat_id or 123,
        access_repo,
        training_repo,
    )

    assert result is False


@pytest.mark.asyncio
async def test_unpaid_request_not_declined(
    access_service, mock_bot, access_repo, training_repo, sample_user, sample_training
):
    """Test that unpaid join request is not declined automatically."""
    result = await access_service.handle_join_request(
        sample_user.id,
        sample_training.telegram_chat_id or 123,
        access_repo,
        training_repo,
    )

    assert result is False
    # No message should be sent
    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_training1_payment_no_access_to_training2(
    access_service, access_repo, training_repo, sample_user
):
    """Test that buying training 1 doesn't grant access to training 2."""
    training1 = await training_repo.create(title="Тренировка №1", price=990, sort_order=1)
    training2 = await training_repo.create(title="Тренировка №2", price=990, sort_order=2)

    await access_repo.grant_access(sample_user.id, training1.id)

    has_access_t1 = await access_repo.has_active_access(sample_user.id, training1.id)
    has_access_t2 = await access_repo.has_active_access(sample_user.id, training2.id)

    assert has_access_t1 is True
    assert has_access_t2 is False


@pytest.mark.asyncio
async def test_forwarded_link_no_access(
    access_service, access_repo, training_repo, sample_user, sample_training
):
    """Test that forwarded invite link doesn't grant access."""
    # User has no access
    result = await access_service.handle_join_request(
        sample_user.id,
        sample_training.telegram_chat_id or 123,
        access_repo,
        training_repo,
    )

    assert result is False


@pytest.mark.asyncio
async def test_repeat_join_works(
    access_service, access_repo, training_repo, sample_user, sample_training
):
    """Test that paid user can rejoin after leaving."""
    await training_repo.update_chat_id(sample_training.id, -1001234567890)
    await access_repo.grant_access(sample_user.id, sample_training.id)

    # Simulate leaving and rejoining
    result = await access_service.handle_join_request(
        sample_user.id,
        -1001234567890,
        access_repo,
        training_repo,
    )

    assert result is True


@pytest.mark.asyncio
async def test_no_admin_rights_handled(
    access_service, mock_bot, training_repo, sample_user, sample_training
):
    """Test that missing admin rights are handled gracefully."""
    await training_repo.update_chat_id(sample_training.id, -1001234567890)
    from aiogram.methods import CreateChatInviteLink

    mock_bot.create_chat_invite_link.side_effect = TelegramAPIError(
        method=CreateChatInviteLink,
        message="Not enough rights",
    )

    invite_repo = AsyncMock()
    result = await access_service.create_invite_link(
        sample_user.id,
        sample_training.id,
        training_repo,
        invite_repo,
    )

    assert result is None
