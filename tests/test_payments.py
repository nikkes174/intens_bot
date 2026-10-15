"""Tests for YooKassa payment integration."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.services.payment import PaymentService
from tgbot.config import YooKassaConfig


@pytest.fixture
def yookassa_config():
    return YooKassaConfig(
        shop_id="test_shop",
        secret_key="test_secret",
        return_url="https://t.me/test_bot",
        vat_code=1,
    )


@pytest.fixture
def payment_service(yookassa_config):
    return PaymentService(yookassa_config)


@pytest.mark.asyncio
async def test_create_payment(payment_service):
    """Test creating a payment."""
    with patch("tgbot.services.payment.Payment.create") as mock_create:
        mock_payment = MagicMock()
        mock_payment.id = "payment_123"
        mock_payment.confirmation.confirmation_url = "https://yoomoney.ru/payment/123"
        mock_create.return_value = mock_payment

        payment_id, url = await payment_service.create_payment(
            user_id=1,
            training_id=1,
            email="test@example.com",
            amount=Decimal("990.00"),
        )

        assert payment_id == "payment_123"
        assert "yoomoney" in url


@pytest.mark.asyncio
async def test_successful_payment_processing(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test processing a successful payment."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {
            "id": "payment_123",
            "status": "succeeded",
            "amount": "990.00",
            "currency": "RUB",
        }

        result = await payment_service.process_payment_succeeded(
            "payment_123",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is True

        # Check access was granted
        has_access = await access_repo.has_active_access(sample_user.id, sample_training.id)
        assert has_access is True


@pytest.mark.asyncio
async def test_canceled_payment(payment_service, order_repo, sample_user, sample_training):
    """Test processing a canceled payment."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_canceled",
    )

    result = await payment_service.process_payment_canceled(
        "payment_canceled",
        order_repo,
    )

    assert result is True

    updated_order = await order_repo.get_by_id(order.id)
    assert updated_order.status == "canceled"


@pytest.mark.asyncio
async def test_duplicate_webhook_ignored(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test that duplicate webhook doesn't create duplicate access."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )
    await order_repo.update_status(order.id, "succeeded")

    # Grant access manually (simulating first webhook)
    await access_repo.grant_access(sample_user.id, sample_training.id, order.id)

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {
            "id": "payment_123",
            "status": "succeeded",
            "amount": "990.00",
            "currency": "RUB",
        }

        result = await payment_service.process_payment_succeeded(
            "payment_123",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is True

        # Access should still be unique (no duplicate)
        accesses = await access_repo.get_user_accesses(sample_user.id)
        training_accesses = [a for a in accesses if a.training_id == sample_training.id]
        assert len(training_accesses) == 1


@pytest.mark.asyncio
async def test_amount_mismatch_rejected(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test that payment with wrong amount is rejected."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {
            "id": "payment_123",
            "status": "succeeded",
            "amount": "500.00",  # Wrong amount
            "currency": "RUB",
        }

        result = await payment_service.process_payment_succeeded(
            "payment_123",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is False


@pytest.mark.asyncio
async def test_currency_mismatch_rejected(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test that payment with wrong currency is rejected."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        currency="RUB",
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {
            "id": "payment_123",
            "status": "succeeded",
            "amount": "990.00",
            "currency": "USD",  # Wrong currency
        }

        result = await payment_service.process_payment_succeeded(
            "payment_123",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is False


@pytest.mark.asyncio
async def test_payment_from_other_order_rejected(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test that payment from different order is rejected."""
    order1 = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    # Different payment ID
    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {
            "id": "payment_456",  # Different ID
            "status": "succeeded",
            "amount": "990.00",
            "currency": "RUB",
        }

        result = await payment_service.process_payment_succeeded(
            "payment_456",  # Different payment ID
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is False


@pytest.mark.asyncio
async def test_yookassa_api_error(payment_service, order_repo, access_repo, sample_user, sample_training):
    """Test handling of YooKassa API error."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = None  # API error

        result = await payment_service.process_payment_succeeded(
            "payment_123",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

        assert result is False
