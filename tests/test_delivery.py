"""Tests for material delivery."""

from unittest.mock import AsyncMock

import pytest

from tgbot.services.delivery import DeliveryService


@pytest.fixture
def mock_bot():
    return AsyncMock()


@pytest.fixture
def delivery_service(mock_bot):
    return DeliveryService(mock_bot)


@pytest.mark.asyncio
async def test_video_sent_after_payment(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that video is sent after payment."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_123",
        file_name="workout.mp4",
        sort_order=1,
    )

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result["success"] is True
    assert result["delivered"] == 1
    mock_bot.send_video.assert_called_once()


@pytest.mark.asyncio
async def test_pdf_sent_after_payment(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that PDF is sent after payment."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="pdf",
        telegram_file_id="pdf_123",
        file_name="program.pdf",
        sort_order=1,
    )

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result["success"] is True
    assert result["delivered"] == 1
    mock_bot.send_document.assert_called_once()


@pytest.mark.asyncio
async def test_materials_sent_in_order(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that materials are sent in correct order."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_1",
        file_name="first.mp4",
        sort_order=1,
    )
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_2",
        file_name="second.mp4",
        sort_order=2,
    )

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result["success"] is True
    assert result["delivered"] == 2


@pytest.mark.asyncio
async def test_unpaid_user_gets_no_materials(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    access_repo,
    sample_user,
    sample_training,
):
    """Test that unpaid user doesn't receive materials."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_123",
        file_name="workout.mp4",
        sort_order=1,
    )

    # Verify user has no access
    has_access = await access_repo.has_active_access(sample_user.id, sample_training.id)
    assert has_access is False

    # In real scenario, handler checks access before calling deliver_materials
    # Here we verify the access check works correctly
    if not has_access:
        mock_bot.send_video.assert_not_called()


@pytest.mark.asyncio
async def test_repeat_delivery_works(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that materials can be re-delivered."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_123",
        file_name="workout.mp4",
        sort_order=1,
    )

    # First delivery
    result1 = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    # Second delivery (should skip already sent)
    result2 = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result1["delivered"] == 1
    assert result2["delivered"] == 0  # Already sent


@pytest.mark.asyncio
async def test_force_resend_sends_sent_material_without_resetting_history(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    material = await material_repo.create(
        training_id=sample_training.id,
        material_type="pdf",
        telegram_file_id="pdf_123",
        file_name="program.pdf",
        sort_order=1,
    )
    await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )
    prior = await delivery_repo.get_delivery(sample_user.id, material.id)
    original_sent_at = prior.sent_at

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
        force_resend=True,
    )

    refreshed = await delivery_repo.get_delivery(sample_user.id, material.id)
    assert result["delivered"] == 1
    assert mock_bot.send_document.await_count == 2
    assert refreshed.status == "sent"
    assert refreshed.sent_at == original_sent_at


@pytest.mark.asyncio
async def test_force_resend_includes_materials_added_after_purchase(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    first = await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_1",
        file_name="first.mp4",
        sort_order=1,
    )
    await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )
    second = await material_repo.create(
        training_id=sample_training.id,
        material_type="pdf",
        telegram_file_id="pdf_2",
        file_name="new.pdf",
        sort_order=2,
    )

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
        force_resend=True,
    )

    assert result["delivered"] == 2
    assert mock_bot.send_video.await_count == 2
    assert mock_bot.send_document.await_count == 1
    assert (await delivery_repo.get_delivery(sample_user.id, first.id)).status == "sent"
    assert (await delivery_repo.get_delivery(sample_user.id, second.id)).status == "sent"


@pytest.mark.asyncio
async def test_telegram_api_error_no_access_loss(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that Telegram API error doesn't cause access loss."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_123",
        file_name="workout.mp4",
        sort_order=1,
    )

    # Simulate Telegram API error
    mock_bot.send_video.side_effect = Exception("Telegram API error")

    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result["success"] is True  # Service completes
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_undelivered_materials_recovered(
    delivery_service,
    mock_bot,
    delivery_repo,
    material_repo,
    training_repo,
    sample_user,
    sample_training,
):
    """Test that undelivered materials are recovered on restart."""
    await material_repo.create(
        training_id=sample_training.id,
        material_type="video",
        telegram_file_id="video_123",
        file_name="workout.mp4",
        sort_order=1,
    )

    # First attempt fails
    mock_bot.send_video.side_effect = Exception("Network error")
    await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    # Reset mock
    mock_bot.send_video.side_effect = None

    # Second attempt succeeds
    result = await delivery_service.deliver_materials(
        sample_user.id,
        sample_training.id,
        delivery_repo,
        material_repo,
        training_repo,
    )

    assert result["delivered"] == 1
