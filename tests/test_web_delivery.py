from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.database import session_scope
from app.models import WebArticleRecord, WebDelivery, WebDetection, WebWatch
from app.services.web_monitor.delivery import configure, process_delivery
from app.services.web_monitor.storage import normalize_url, url_hash


@pytest.mark.asyncio
async def test_delivery_marks_sent_only_after_telegram_success(monkeypatch):
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов")
        db.add(watch)
        await db.flush()
        article = WebArticleRecord(
            provider="test",
            title="Иван Иванов",
            url="https://test.example/a",
            canonical_url=normalize_url("https://test.example/a"),
            url_hash=url_hash("https://test.example/a"),
            source_name="Test",
            description="Описание",
            published_at=datetime.now(UTC),
        )
        db.add(article)
        await db.flush()
        detection = WebDetection(article_id=article.id, watch_id=watch.id, matched_text="иван иванов")
        db.add(detection)
        await db.flush()
        delivery = WebDelivery(detection_id=detection.id, admin_id=1)
        db.add(delivery)
        await db.commit()
        delivery_id = delivery.id

    bot = AsyncMock()
    configure(bot=bot, admin_id=1)
    monkeypatch.setattr("app.services.web_monitor.delivery.notifications_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr("app.services.web_monitor.delivery.send_notification", AsyncMock())
    assert await process_delivery(delivery_id) is True
    async with session_scope() as db:
        saved = await db.get(WebDelivery, delivery_id)
        assert saved.status == "sent"
        assert saved.delivered_at is not None
