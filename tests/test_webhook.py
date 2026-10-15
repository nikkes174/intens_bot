"""Tests for YooKassa webhook endpoint."""

import pytest
from fastapi.testclient import TestClient

from bot import create_app
from tgbot.config import Config


@pytest.fixture
def app():
    config = Config(
        tg_bot=type("TgBot", (), {"token": "test", "admin_ids": [123]})(),
        db=type("DbConfig", (), {"url": "sqlite+aiosqlite:///test.db"})(),
        yookassa=type(
            "YooKassaConfig",
            (),
            {
                "shop_id": "test",
                "secret_key": "test",
                "return_url": "https://t.me/",
                "vat_code": 1,
            },
        )(),
        webhook=type("WebhookConfig", (), {"host": "0.0.0.0", "port": 8000})(),
        misc=type("Miscellaneous", (), {"log_level": "INFO"})(),
    )
    return create_app(config)


@pytest.fixture
def client(app):
    return TestClient(app)


def test_health_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_invalid_json(client):
    """Test webhook with invalid JSON."""
    response = client.post("/webhooks/yookassa", content="invalid json")
    assert response.status_code in [400, 422]


def test_webhook_missing_event(client):
    """Test webhook with missing event."""
    response = client.post("/webhooks/yookassa", json={"object": {"id": "123"}})
    assert response.status_code == 400


def test_webhook_missing_payment_id(client):
    """Test webhook with missing payment_id."""
    response = client.post("/webhooks/yookassa", json={"event": "payment.succeeded", "object": {}})
    assert response.status_code == 400
