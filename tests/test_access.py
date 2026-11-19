from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User

from tgbot.middlewares.access import AccessMiddleware


def make_user_update(user_id: int, *, text: str | None = None, photo: bool = False) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        photo=[PhotoSize(file_id="file-id", file_unique_id="unique-id", width=1, height=1)]
        if photo
        else None,
    )
    return Update(update_id=user_id, message=message)


def make_callback_update(user_id: int) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="result",
    )
    callback = CallbackQuery(
        id="callback-id",
        from_user=user,
        chat_instance=str(user_id),
        data="analyze_again",
        message=message,
    )
    return Update(update_id=user_id, callback_query=callback)


async def invoke_middleware(update: Update, allowed_ids: set[int]) -> list[Update]:
    called: list[Update] = []

    async def handler(event: Update, data: dict) -> None:
        called.append(event)

    await AccessMiddleware(allowed_ids)(handler, update, {})
    return called


@pytest.mark.asyncio
async def test_one_allowed_id_gets_access() -> None:
    update = make_user_update(123)

    called = await invoke_middleware(update, {123})

    assert called == [update]


@pytest.mark.asyncio
async def test_multiple_allowed_ids_get_access() -> None:
    first = make_user_update(123)
    second = make_user_update(456)

    assert await invoke_middleware(first, {123, 456}) == [first]
    assert await invoke_middleware(second, {123, 456}) == [second]


@pytest.mark.asyncio
async def test_unknown_id_is_completely_ignored() -> None:
    called = await invoke_middleware(make_user_update(999), {123})

    assert called == []


@pytest.mark.asyncio
async def test_start_from_unknown_id_is_ignored() -> None:
    called = await invoke_middleware(make_user_update(999, text="/start"), {123})

    assert called == []


@pytest.mark.asyncio
async def test_photo_from_unknown_id_is_not_processed() -> None:
    update = make_user_update(999, photo=True)
    download = AsyncMock()
    openrouter = AsyncMock()

    async def handler(event: Update, data: dict) -> None:
        await download()
        await openrouter()

    await AccessMiddleware({123})(handler, update, {})

    download.assert_not_awaited()
    openrouter.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_from_unknown_id_is_ignored_without_answer() -> None:
    update = make_callback_update(999)
    callback_answer = AsyncMock()

    async def handler(event: Update, data: dict) -> None:
        await event.callback_query.answer()

    with patch.object(CallbackQuery, "answer", callback_answer):
        await AccessMiddleware({123})(handler, update, {})

    callback_answer.assert_not_awaited()
