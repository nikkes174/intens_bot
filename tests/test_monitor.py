import pytest

from app.services.monitor import get_active_keywords, process_message, save_detection
from app.database import session_scope
from app.models import Mention


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
