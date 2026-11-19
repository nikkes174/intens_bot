import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from tgbot.handlers import photo as photo_handlers
from tgbot.handlers.photo import FoodStates
from tgbot.handlers.start import start_handler
from tgbot.services.openrouter import OpenRouterRateLimitError


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id
        self.text = ""
        self.reply_markup = None

    async def edit_text(self, text: str, reply_markup=None) -> None:
        self.text = text
        self.reply_markup = reply_markup


class FakeMessage:
    def __init__(self, user_id: int, message_id: int = 1) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.photo = [SimpleNamespace(file_id="file-id", file_size=1)]
        self.document = None
        self.message_id = message_id
        self.answers: list[tuple[str, object]] = []
        self._next_message_id = message_id + 100

    async def answer(self, text: str, reply_markup=None) -> FakeSentMessage:
        self.answers.append((text, reply_markup))
        sent_message = FakeSentMessage(self._next_message_id)
        self._next_message_id += 1
        return sent_message


class FakeBot:
    async def get_file(self, file_id: str) -> object:
        return object()

    async def download(self, telegram_file: object, destination) -> None:
        destination.write(b"image")


def make_state(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage, key)


def fake_prepare_image(image_bytes: bytes) -> bytes:
    return b"prepared"


@pytest.mark.asyncio
async def test_new_photo_clears_waiting_weight_state(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = 90101
    state = make_state(user_id)
    await state.set_state(FoodStates.waiting_for_weight)
    await state.update_data(last_analysis={"old": "analysis"})

    analysis = SimpleNamespace(recognized=False)
    monkeypatch.setattr(photo_handlers, "prepare_image", fake_prepare_image)
    monkeypatch.setattr(photo_handlers, "analyze_food", AsyncMock(return_value=analysis))

    await photo_handlers._handle_image(FakeMessage(user_id), state, FakeBot(), object())

    assert await state.get_state() is None
    assert (await state.get_data())[photo_handlers.LAST_ANALYSIS_KEY] is None


@pytest.mark.asyncio
async def test_start_clears_fsm() -> None:
    user_id = 90102
    state = make_state(user_id)
    await state.set_state(FoodStates.waiting_for_weight)
    await state.update_data(last_analysis={"old": "analysis"})
    message = FakeMessage(user_id)

    await start_handler(message, state)

    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_recognize_button_clears_fsm() -> None:
    user_id = 90106
    state = make_state(user_id)
    await state.set_state(FoodStates.waiting_for_weight)
    await state.update_data(last_analysis={"old": "analysis"})

    await photo_handlers.ask_for_photo(FakeMessage(user_id), state)

    assert await state.get_state() is None
    assert await state.get_data() == {}


@pytest.mark.asyncio
async def test_old_inline_keyboard_is_rejected() -> None:
    user_id = 90103
    state = make_state(user_id)
    await state.update_data(
        active_result_message_id=20,
        active_result_user_id=user_id,
        active_result_chat_id=user_id,
        last_analysis={"recognized": True},
    )
    callback_message = SimpleNamespace(
        message_id=10,
        chat=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=callback_message,
        answer=AsyncMock(),
    )

    await photo_handlers.analyze_again_handler(callback, state)

    callback_message.answer.assert_awaited_once_with(photo_handlers.STALE_RESULT_TEXT)
    assert (await state.get_data())[photo_handlers.LAST_ANALYSIS_KEY] is not None


@pytest.mark.asyncio
async def test_inline_keyboard_checks_user_ownership() -> None:
    user_id = 90107
    state = make_state(user_id)
    await state.update_data(
        active_result_message_id=20,
        active_result_user_id=user_id,
        active_result_chat_id=user_id,
        last_analysis={"recognized": True},
    )
    callback_message = SimpleNamespace(
        message_id=20,
        chat=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id + 1),
        message=callback_message,
        answer=AsyncMock(),
    )

    await photo_handlers.refine_weight_handler(callback, state)

    callback_message.answer.assert_awaited_once_with(photo_handlers.STALE_RESULT_TEXT)
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_only_one_photo_per_user_is_analyzed(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = 90104
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def analyze(image_bytes: bytes, client: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        first_started.set()
        await release_first.wait()
        return SimpleNamespace(recognized=False)

    monkeypatch.setattr(photo_handlers, "prepare_image", fake_prepare_image)
    monkeypatch.setattr(photo_handlers, "analyze_food", analyze)
    first_state = make_state(user_id)
    second_state = make_state(user_id)
    first_message = FakeMessage(user_id, message_id=1)
    second_message = FakeMessage(user_id, message_id=2)

    first_task = asyncio.create_task(
        photo_handlers._handle_image(first_message, first_state, FakeBot(), object())
    )
    await first_started.wait()
    await photo_handlers._handle_image(second_message, second_state, FakeBot(), object())
    release_first.set()
    await first_task

    assert calls == 1
    assert any(photo_handlers.BUSY_TEXT in text for text, _ in second_message.answers)


@pytest.mark.asyncio
async def test_error_releases_per_user_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = 90105
    calls = 0

    async def analyze(image_bytes: bytes, client: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise OpenRouterRateLimitError

    monkeypatch.setattr(photo_handlers, "prepare_image", fake_prepare_image)
    monkeypatch.setattr(photo_handlers, "analyze_food", analyze)

    await photo_handlers._handle_image(
        FakeMessage(user_id, 1), make_state(user_id), FakeBot(), object()
    )
    await photo_handlers._handle_image(
        FakeMessage(user_id, 2), make_state(user_id), FakeBot(), object()
    )

    assert calls == 2
