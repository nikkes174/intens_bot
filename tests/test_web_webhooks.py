import hashlib
import hmac

from app.config import get_settings
from app.services.web_monitor.webhooks import _authorized, parse_webhook_articles


def test_webhook_payload_requires_title_and_url():
    articles = parse_webhook_articles(
        "partner",
        {"articles": [{"title": "Иван Иванов", "url": "https://example.test/a"}]},
    )
    assert articles[0].provider == "partner"


def test_webhook_requires_configured_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "web_webhook_secrets", "partner=secret")
    body = b'{"title":"x","url":"https://example.test/a"}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert _authorized("partner", body, {"X-Hub-Signature-256": f"sha256={signature}"})
    assert not _authorized("partner", body, {"X-Webhook-Token": "wrong"})
