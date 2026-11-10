import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import session_scope
from app.models import WebDelivery, WebDetection, WebWatch
from app.services.web_monitor.webhooks import (
    _authorized,
    ingest_webhook,
    parse_webhook_articles,
    parse_websub_xml,
)


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


def test_websub_parses_rss_xml():
    xml = """<rss><channel><item>
      <title>Иван Иванов выступил</title>
      <link>https://example.test/news/1</link>
      <description>Москва</description>
      <pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate>
      <source>Example</source>
    </item></channel></rss>""".encode()
    articles = parse_websub_xml("websub", xml)
    assert articles[0].title == "Иван Иванов выступил"
    assert articles[0].source_name == "Example"
    assert articles[0].published_at == datetime(2026, 11, 10, 10, tzinfo=UTC)


def test_websub_parses_atom_xml():
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Иван Иванов в новостях</title>
      <link href="https://example.test/atom/1"/>
      <summary>Описание</summary>
      <updated>2026-11-10T10:00:00Z</updated>
    </entry></feed>'''.encode()
    articles = parse_websub_xml("websub", xml)
    assert articles[0].url == "https://example.test/atom/1"


@pytest.mark.asyncio
async def test_repeated_websub_delivery_does_not_create_duplicate_detection(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_id", 1)
    async with session_scope() as db:
        db.add(WebWatch(query="иван иванов", enabled=True))
        await db.commit()
    monkeypatch.setattr(
        "app.services.web_monitor.webhooks.notifications_enabled",
        lambda: __import__("asyncio").sleep(0, result=True),
    )
    xml = """<rss><channel><item>
      <title>Иван Иванов выступил</title><link>https://example.test/repeat</link>
    </item></channel></rss>""".encode()
    await ingest_webhook("websub", xml)
    await ingest_webhook("websub", xml)
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebDetection.id))) == 1
        assert await db.scalar(select(func.count(WebDelivery.id))) == 1
