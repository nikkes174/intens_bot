import json
from datetime import UTC, datetime

import pytest

from app.database import session_scope
from app.models import Delivery, Detection, Mention
from app.services.monitor import (
    _process_delivery,
    get_active_keywords,
    process_message,
    save_detection,
)


@pytest.mark.asyncio
async def test_save_detection_duplicate():
    assert await save_detection(1, 10, ["foo"]) is True
    assert await save_detection(1, 10, ["bar"]) is False


@pytest.mark.asyncio
async def test_active_keywords():
    async with session_scope() as db:
        db.add(Mention(keyword="Foo", enabled=True))
        db.add(Mention(keyword="bar", enabled=False))
        await db.commit()
    keywords = await get_active_keywords()
    assert keywords == ["Foo"]


@pytest.mark.asyncio
async def test_process_message_no_match(monkeypatch):
    async def mon():
        return True

    async def allowed(x):
        return True

    async def fake_keywords():
        return ["zzz"]

    monkeypatch.setattr("app.services.monitor.monitoring_enabled", mon)
    monkeypatch.setattr("app.services.monitor.source_allowed", allowed)
    monkeypatch.setattr("app.services.monitor.get_active_keywords", fake_keywords)
    result = await process_message(1, 1, "hello", None)
    assert result is False


@pytest.mark.asyncio
async def test_process_message_duplicate_event(monkeypatch):
    async def mon():
        return True

    async def allowed(x):
        return True

    async def keywords():
        return ["foo"]

    calls = {"n": 0}

    async def fake_enqueue(**kwargs):
        calls["n"] += 1
        return 1 if calls["n"] == 1 else None

    monkeypatch.setattr("app.services.monitor.monitoring_enabled", mon)
    monkeypatch.setattr("app.services.monitor.source_allowed", allowed)
    monkeypatch.setattr("app.services.monitor.get_active_keywords", keywords)
    monkeypatch.setattr("app.services.monitor.enqueue_notification", fake_enqueue)
    assert await process_message(2, 20, "foo", None) is True
    assert await process_message(2, 20, "foo", None) is False


@pytest.mark.asyncio
async def test_notification_retries_only_failed_admin_without_blocking_others(monkeypatch):
    async with session_scope() as db:
        detection = Detection(source_id=1, message_id=30, matched_keywords='["foo"]')
        db.add(detection)
        await db.flush()
        delivery = Delivery(
            detection_id=detection.id,
            status="pending",
            payload=json.dumps(
                {
                    "source_id": 1,
                    "title": "Новое сообщение",
                    "matches": ["foo"],
                    "text": "Текст сообщения",
                    "published_at": datetime.now(UTC).isoformat(),
                    "audience": None,
                    "url": None,
                },
                ensure_ascii=False,
            ),
        )
        db.add(delivery)
        await db.commit()
        delivery_id = delivery.id

    calls = []

    async def send(_bot, admin_id, _text, _keyboard):
        calls.append(admin_id)
        if admin_id == 101:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr("app.services.monitor._bot", object())
    monkeypatch.setattr("app.services.monitor._admin_ids", (101, 202, 202))
    monkeypatch.setattr("app.services.monitor.send_notification", send)
    await _process_delivery(delivery_id)

    assert calls == [101, 202]
    async with session_scope() as db:
        saved = await db.get(Delivery, delivery_id)
        assert saved.status == "pending"
        assert json.loads(saved.payload)["_sent_admin_ids"] == [202]
        saved.next_retry_at = None
        await db.commit()

    async def send_after_retry(_bot, admin_id, _text, _keyboard):
        calls.append(admin_id)

    monkeypatch.setattr("app.services.monitor.send_notification", send_after_retry)
    await _process_delivery(delivery_id)

    assert calls == [101, 202, 101]
    async with session_scope() as db:
        saved = await db.get(Delivery, delivery_id)
        assert saved.status == "sent"
