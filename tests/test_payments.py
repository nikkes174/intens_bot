"""Tests for YooKassa payment integration."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from environs import Env

from tgbot.config import YooKassaConfig
from tgbot.services.payment import PaymentService


def verified_payment(order, payment_id, *, amount=None, currency="RUB", metadata=None):
    return {
        "id": payment_id,
        "status": "succeeded",
        "amount": str(order.amount if amount is None else amount),
        "currency": currency,
        "metadata": metadata
        or {
            "user_id": str(order.user_id),
            "training_id": str(order.training_id),
            "order_id": str(order.id),
            "metadata_version": "2",
        },
    }


@pytest.fixture
def yookassa_config():
    return YooKassaConfig(
        shop_id="test_shop",
        secret_key="test_secret",
        return_url="https://t.me/test_bot",
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
            order_id=42,
            email="test@example.com",
            amount=Decimal("990.00"),
        )

        assert payment_id == "payment_123"
        assert "yoomoney" in url
        assert mock_create.call_args.args[0]["metadata"]["order_id"] == "42"
        assert mock_create.call_args.args[0]["metadata"]["metadata_version"] == "2"
        assert mock_create.call_args.args[0]["receipt"]["items"][0]["vat_code"] == 1


def test_yookassa_config_does_not_require_vat_env(monkeypatch):
    monkeypatch.delenv("YOOKASSA_VAT_CODE", raising=False)

    config = YooKassaConfig.from_env(Env())

    assert not hasattr(config, "vat_code")


@pytest.mark.asyncio
async def test_successful_payment_processing(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    """Test processing a successful payment."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = verified_payment(order, "payment_123")

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

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = {"id": "payment_canceled", "status": "canceled"}
        result = await payment_service.process_payment_canceled("payment_canceled", order_repo)

    assert result is True

    updated_order = await order_repo.get_by_id(order.id)
    assert updated_order.status == "canceled"


@pytest.mark.asyncio
async def test_temporary_status_does_not_mark_payment_canceled(
    payment_service, order_repo, sample_user, sample_training
):
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_pending",
    )
    with patch.object(
        payment_service,
        "get_payment_status",
        return_value={"id": "payment_pending", "status": "pending"},
    ):
        result = await payment_service.process_payment_canceled("payment_pending", order_repo)

    assert result is False
    assert (await order_repo.get_by_id(order.id)).status == "pending"


@pytest.mark.asyncio
async def test_duplicate_webhook_ignored(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
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
        mock_status.return_value = verified_payment(order, "payment_123")

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
async def test_amount_mismatch_rejected(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    """Test that payment with wrong amount is rejected."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = verified_payment(order, "payment_123", amount="500.00")

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
async def test_currency_mismatch_rejected(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    """Test that payment with wrong currency is rejected."""
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        currency="RUB",
        payment_id="payment_123",
    )

    with patch.object(payment_service, "get_payment_status") as mock_status:
        mock_status.return_value = verified_payment(order, "payment_123", currency="USD")

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
@pytest.mark.parametrize(
    "metadata_field", ["user_id", "training_id", "order_id", "missing_order_id"]
)
async def test_metadata_mismatch_rejects_payment(
    metadata_field,
    payment_service,
    order_repo,
    access_repo,
    sample_user,
    sample_training,
):
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_metadata",
    )
    metadata = verified_payment(order, "payment_metadata")["metadata"]
    if metadata_field == "missing_order_id":
        metadata.pop("order_id")
    else:
        metadata[metadata_field] = "999999"

    with patch.object(
        payment_service,
        "get_payment_status",
        return_value=verified_payment(order, "payment_metadata", metadata=metadata),
    ):
        result = await payment_service.process_payment_succeeded(
            "payment_metadata",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

    assert result is False
    assert not await access_repo.has_active_access(sample_user.id, sample_training.id)


@pytest.mark.asyncio
async def test_yookassa_payment_id_must_match_authoritative_response(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="payment_metadata",
    )
    info = verified_payment(order, "payment_metadata")
    info["id"] = "different-payment"
    with patch.object(payment_service, "get_payment_status", return_value=info):
        result = await payment_service.process_payment_succeeded(
            "payment_metadata",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=AsyncMock(),
        )

    assert result is False
    assert not await access_repo.has_active_access(sample_user.id, sample_training.id)


@pytest.mark.asyncio
async def test_legacy_payment_metadata_is_verified_against_saved_payment_link(
    payment_service, order_repo, access_repo, sample_user, sample_training, training_repo
):
    order = await order_repo.create(
        user_id=sample_user.id,
        training_id=sample_training.id,
        amount=Decimal("990.00"),
        payment_id="legacy-payment",
        idempotency_key="old-idempotency-key",
        payment_metadata_version="legacy",
    )
    legacy_metadata = {
        "user_id": str(sample_user.id),
        "training_id": str(sample_training.id),
        "idempotency_key": "old-idempotency-key",
    }
    with patch.object(
        payment_service,
        "get_payment_status",
        return_value=verified_payment(order, "legacy-payment", metadata=legacy_metadata),
    ):
        result = await payment_service.process_payment_succeeded(
            "legacy-payment",
            bot=AsyncMock(),
            order_repo=order_repo,
            access_repo=access_repo,
            outbox_repo=AsyncMock(),
            user_repo=AsyncMock(),
            training_repo=training_repo,
        )

    assert result is True
    assert await access_repo.has_active_access(sample_user.id, sample_training.id)


@pytest.mark.asyncio
async def test_payment_from_other_order_rejected(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    """Test that payment from different order is rejected."""
    await order_repo.create(
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
async def test_yookassa_api_error(
    payment_service, order_repo, access_repo, sample_user, sample_training
):
    """Test handling of YooKassa API error."""
    await order_repo.create(
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
