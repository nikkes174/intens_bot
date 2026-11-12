from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.admin.keyboards import confirm_delete_source_keyboard, source_actions_keyboard
from app.database import session_scope
from app.models import Detection, RemovedSource, Source
from app.userbot import sources

TELEGRAM_ID = -1001234567890


def test_source_card_uses_delete_confirmation():
    actions = source_actions_keyboard(17)
    assert any(
        button.text == "🗑 Удалить канал" and button.callback_data == "source:delete:17"
        for row in actions.inline_keyboard
        for button in row
    )

    confirmation = confirm_delete_source_keyboard(17)
    callbacks = {
        button.callback_data
        for row in confirmation.inline_keyboard
        for button in row
    }
    assert callbacks == {"source:delete_confirm:17", "source:delete_cancel:17"}


@pytest.mark.asyncio
async def test_delete_source_removes_registry_but_keeps_history_and_tombstone():
    async with session_scope() as db:
        source = Source(telegram_id=TELEGRAM_ID, title="Удалённый канал")
        db.add(source)
        await db.flush()
        db.add(
            Detection(
                source_id=TELEGRAM_ID,
                message_id=7,
                matched_keywords="[\"Иван Иванов\"]",
            )
        )
        source_db_id = source.id
        await db.commit()

    sources._source_cache[TELEGRAM_ID] = {"time": 0, "audience": 42}

    assert await sources.delete_source(source_db_id) == TELEGRAM_ID

    async with session_scope() as db:
        assert await db.get(Source, source_db_id) is None
        tombstone = await db.scalar(
            select(RemovedSource).where(RemovedSource.telegram_id == TELEGRAM_ID)
        )
        assert tombstone is not None
        assert await db.scalar(select(func.count(Detection.id))) == 1

    assert TELEGRAM_ID not in sources._source_cache
    assert await sources.count_sources() == 0


@pytest.mark.asyncio
async def test_deleted_source_is_not_recreated_from_new_message():
    async with session_scope() as db:
        db.add(RemovedSource(telegram_id=TELEGRAM_ID))
        await db.commit()

    event = SimpleNamespace(
        chat_id=TELEGRAM_ID,
        chat=SimpleNamespace(
            title="Удалённый канал",
            username="removed_channel",
            broadcast=True,
            megagroup=False,
        ),
        is_group=False,
    )

    assert await sources.ensure_source_from_event(event) is False

    async with session_scope() as db:
        assert await db.scalar(select(Source).where(Source.telegram_id == TELEGRAM_ID)) is None
        assert (
            await db.scalar(
                select(RemovedSource).where(RemovedSource.telegram_id == TELEGRAM_ID)
            )
            is not None
        )


@pytest.mark.asyncio
async def test_manual_link_restores_deleted_source(monkeypatch):
    async with session_scope() as db:
        db.add(RemovedSource(telegram_id=TELEGRAM_ID))
        await db.commit()

    monkeypatch.setattr(sources, "get_peer_id", lambda entity: TELEGRAM_ID)
    client = AsyncMock()
    client.get_entity = AsyncMock(
        return_value=SimpleNamespace(
            id=123,
            title="Восстановленный канал",
            username="restored_channel",
            broadcast=True,
            megagroup=False,
            participants_count=10,
        )
    )
    client.return_value = None

    restored = await sources.join_source(client, "https://t.me/restored_channel")

    assert restored.telegram_id == TELEGRAM_ID
    async with session_scope() as db:
        assert await db.scalar(
            select(RemovedSource).where(RemovedSource.telegram_id == TELEGRAM_ID)
        ) is None
        source = await db.scalar(select(Source).where(Source.telegram_id == TELEGRAM_ID))
        assert source is not None
        assert source.enabled is True
        assert source.excluded is False
