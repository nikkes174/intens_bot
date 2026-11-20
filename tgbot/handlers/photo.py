import asyncio
import logging
import random
import re
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from tgbot.keyboards.main import (
    SHOW_FOOD_BUTTON_TEXT,
    analysis_keyboard,
    main_menu_keyboard,
    retry_keyboard,
)
from tgbot.schemas.food import FoodAnalysis
from tgbot.services.food_analyzer import (
    analyze_food,
    format_food_result,
    format_no_food_result,
    rescale_analysis,
)
from tgbot.services.image_processor import (
    MAX_IMAGE_BYTES,
    ImageProcessingError,
    prepare_image,
)
from tgbot.services.openrouter import (
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterError,
    OpenRouterPaymentError,
    OpenRouterRateLimitError,
    OpenRouterRequestError,
    OpenRouterServerError,
    OpenRouterTimeoutError,
    OpenRouterUnavailableError,
)

logger = logging.getLogger(__name__)
router = Router(name="photo")

ANALYZING_TEXTS = (
    "Сейчас посмотрим, что здесь интересного. 🔍",
    "Вика, одну секунду. Изучаю состав.",
    "Выглядит интересно. Сейчас разберёмся.",
    "Так, давай посмотрим, что у нас здесь.",
    "Уже разбираюсь. Обещаю ничего не перепутать. 😉",
)
PHOTO_REQUEST_TEXT = "Отлично, Вика. Жду фотографию.\n\nПосмотрим, что сегодня в меню. 🍽"
ANOTHER_PHOTO_TEXT = "Конечно. Присылай следующую фотографию. 🌿"
GENERIC_ERROR_TEXT = (
    "Вика, сейчас что-то пошло не так.\n\nПопробуй отправить фото ещё раз.\nРазберёмся."
)
BUSY_TEXT = (
    "Секунду, я ещё разбираюсь\nс предыдущим снимком.\n\nСкоро можно будет отправить следующий."
)
STALE_RESULT_TEXT = "Мы уже перешли к другому анализу.\n\nДавай продолжим с последним результатом."
CANCELLED_TEXT = "Хорошо, этот анализ отменяем."
WEIGHT_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?$")

LAST_ANALYSIS_KEY = "last_analysis"
ACTIVE_MESSAGE_ID_KEY = "active_result_message_id"
ACTIVE_USER_ID_KEY = "active_result_user_id"
ACTIVE_CHAT_ID_KEY = "active_result_chat_id"


@dataclass
class _UserRuntime:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    task: asyncio.Task | None = None
    analysis_message: Message | None = None


_USER_STATES: dict[int, _UserRuntime] = {}


class FoodStates(StatesGroup):
    waiting_for_weight = State()


def _user_id(message: Message) -> int:
    return message.from_user.id if message.from_user else message.chat.id


def _cleanup_inactive_user_states() -> None:
    for user_id, runtime in list(_USER_STATES.items()):
        if runtime.task is None and not runtime.lock.locked():
            _USER_STATES.pop(user_id, None)


def _get_user_runtime(user_id: int) -> _UserRuntime:
    _cleanup_inactive_user_states()
    return _USER_STATES.setdefault(user_id, _UserRuntime())


def invalidate_user_flow(user_id: int) -> int:
    runtime = _get_user_runtime(user_id)
    runtime.generation += 1
    return runtime.generation


async def reset_user_flow(state: FSMContext, user_id: int) -> None:
    runtime = _USER_STATES.get(user_id)
    if runtime is not None:
        runtime.generation += 1
    await state.clear()

    if runtime is not None:
        task = runtime.task
        current_task = asyncio.current_task()
        if task is not None and task is not current_task:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
    _cleanup_inactive_user_states()


def _is_current_generation(user_id: int, generation: int, runtime: _UserRuntime) -> bool:
    return _USER_STATES.get(user_id) is runtime and runtime.generation == generation


async def _mark_analysis_cancelled(analysis_message: Message | None) -> None:
    if analysis_message is None:
        return
    try:
        await _edit_result(analysis_message, CANCELLED_TEXT, None)
    except Exception:
        logger.warning("Could not update cancelled analysis message")


async def _store_result(
    state: FSMContext,
    result_message: Message,
    analysis: FoodAnalysis | None,
    user_id: int,
    chat_id: int,
) -> None:
    await state.set_state(None)
    await state.update_data(
        **{
            LAST_ANALYSIS_KEY: analysis.model_dump() if analysis else None,
            ACTIVE_MESSAGE_ID_KEY: result_message.message_id,
            ACTIVE_USER_ID_KEY: user_id,
            ACTIVE_CHAT_ID_KEY: chat_id,
        }
    )


def _is_too_large(size: int | None) -> bool:
    return size is not None and size > MAX_IMAGE_BYTES


async def _download_photo(message: Message, bot) -> bytes:
    if message.photo:
        telegram_file = await bot.get_file(message.photo[-1].file_id)
        source_size = message.photo[-1].file_size
    elif message.document:
        telegram_file = await bot.get_file(message.document.file_id)
        source_size = message.document.file_size
    else:
        raise ImageProcessingError("В сообщении нет изображения")

    if _is_too_large(source_size):
        raise ImageProcessingError("Изображение слишком большое")

    image_buffer = BytesIO()
    await bot.download(telegram_file, destination=image_buffer)
    image_bytes = image_buffer.getvalue()
    if _is_too_large(len(image_bytes)):
        raise ImageProcessingError("Изображение слишком большое")
    return image_bytes


async def _edit_result(message: Message, text: str, reply_markup) -> Message:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return message
    except TelegramBadRequest:
        return await message.answer(text, reply_markup=reply_markup)


def _openrouter_error_text(error: OpenRouterError) -> str:
    if isinstance(error, OpenRouterAuthError):
        return "⚠️ Неверный API-ключ OpenRouter или недостаточно прав доступа."
    if isinstance(error, OpenRouterPaymentError):
        return GENERIC_ERROR_TEXT
    if isinstance(error, OpenRouterRateLimitError):
        return "Похоже, сервис немного перегружен.\n\nДавай попробуем чуть позже."
    if isinstance(error, OpenRouterRequestError):
        return GENERIC_ERROR_TEXT
    if isinstance(error, OpenRouterTimeoutError):
        return GENERIC_ERROR_TEXT
    if isinstance(error, (OpenRouterServerError, OpenRouterUnavailableError)):
        return GENERIC_ERROR_TEXT
    return GENERIC_ERROR_TEXT


async def _is_current_result(callback: CallbackQuery, state: FSMContext) -> bool:
    callback_message = callback.message
    if callback_message is None:
        await callback.answer()
        return False

    data = await state.get_data()
    is_current = (
        data.get(ACTIVE_MESSAGE_ID_KEY) == callback_message.message_id
        and data.get(ACTIVE_USER_ID_KEY) == callback.from_user.id
        and data.get(ACTIVE_CHAT_ID_KEY) == callback_message.chat.id
    )
    await callback.answer()
    if not is_current:
        await callback_message.answer(STALE_RESULT_TEXT)
        return False
    return True


async def _handle_image(
    message: Message,
    state: FSMContext,
    bot,
    openrouter: OpenRouterClient,
) -> None:
    user_id = _user_id(message)
    _cleanup_inactive_user_states()
    runtime = _get_user_runtime(user_id)
    if runtime.lock.locked():
        await message.answer(BUSY_TEXT)
        return

    try:
        async with runtime.lock:
            generation = invalidate_user_flow(user_id)
            runtime.task = asyncio.current_task()
            await state.clear()
            analysis_message: Message | None = None
            image_bytes: bytes | None = None
            prepared_image: bytes | None = None

            try:
                analysis_message = await message.answer(random.choice(ANALYZING_TEXTS))
                runtime.analysis_message = analysis_message
                image_bytes = await _download_photo(message, bot)
                prepared_image = prepare_image(image_bytes)
                analysis = await analyze_food(prepared_image, openrouter)

                if not _is_current_generation(user_id, generation, runtime):
                    await _mark_analysis_cancelled(analysis_message)
                    return

                if not analysis.recognized:
                    result_message = await _edit_result(
                        analysis_message, format_no_food_result(), retry_keyboard()
                    )
                    await _store_result(state, result_message, None, user_id, message.chat.id)
                    return

                result_message = await _edit_result(
                    analysis_message, format_food_result(analysis), analysis_keyboard()
                )
                await _store_result(state, result_message, analysis, user_id, message.chat.id)
            except asyncio.CancelledError:
                await _mark_analysis_cancelled(analysis_message)
                raise
            except ImageProcessingError as error:
                if not _is_current_generation(user_id, generation, runtime):
                    await _mark_analysis_cancelled(analysis_message)
                    return
                if str(error) == "Изображение слишком большое":
                    text = (
                        "Фото оказалось слишком большим.\n\n"
                        "Попробуй отправить его обычной фотографией через Telegram."
                    )
                else:
                    text = GENERIC_ERROR_TEXT
                logger.info("Image processing failed: %s", error.__class__.__name__)
                result_message = await _edit_result(analysis_message, text, retry_keyboard())
                await _store_result(state, result_message, None, user_id, message.chat.id)
            except OpenRouterError as error:
                if not _is_current_generation(user_id, generation, runtime):
                    await _mark_analysis_cancelled(analysis_message)
                    return
                logger.warning("OpenRouter request failed: %s", error.__class__.__name__)
                result_message = await _edit_result(
                    analysis_message, _openrouter_error_text(error), retry_keyboard()
                )
                await _store_result(state, result_message, None, user_id, message.chat.id)
            except Exception:
                if not _is_current_generation(user_id, generation, runtime):
                    await _mark_analysis_cancelled(analysis_message)
                    return
                logger.exception("Unexpected food analysis error")
                result_message = await _edit_result(
                    analysis_message, GENERIC_ERROR_TEXT, retry_keyboard()
                )
                await _store_result(state, result_message, None, user_id, message.chat.id)
            finally:
                del image_bytes
                del prepared_image
                runtime.analysis_message = None
                if runtime.task is asyncio.current_task():
                    runtime.task = None
    finally:
        _cleanup_inactive_user_states()


@router.message(F.text == SHOW_FOOD_BUTTON_TEXT)
async def ask_for_photo(message: Message, state: FSMContext) -> None:
    await reset_user_flow(state, _user_id(message))
    await message.answer(PHOTO_REQUEST_TEXT, reply_markup=main_menu_keyboard())


@router.message(F.photo)
async def photo_handler(
    message: Message, state: FSMContext, bot, openrouter: OpenRouterClient
) -> None:
    await _handle_image(message, state, bot, openrouter)


@router.message(F.document)
async def image_document_handler(
    message: Message, state: FSMContext, bot, openrouter: OpenRouterClient
) -> None:
    if message.document and message.document.mime_type not in {"image/jpeg", "image/png"}:
        return
    await _handle_image(message, state, bot, openrouter)


@router.callback_query(F.data == "analyze_again")
async def analyze_again_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_current_result(callback, state):
        return
    await reset_user_flow(state, callback.from_user.id)
    await callback.message.answer(ANOTHER_PHOTO_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "refine_weight")
async def refine_weight_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _is_current_result(callback, state):
        return
    data = await state.get_data()
    if not data.get(LAST_ANALYSIS_KEY):
        await callback.message.answer(
            "Сначала пришли фотографию — тогда будет что уточнять.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await state.set_state(FoodStates.waiting_for_weight)
    await callback.message.answer(
        "Вика, давай уточним вес.\n\n"
        "Сколько граммов было в порции?\n\n"
        "Просто отправь число, например 350."
    )


@router.message(FoodStates.waiting_for_weight)
async def weight_handler(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().replace(" ", "")
    if not WEIGHT_PATTERN.fullmatch(value):
        await message.answer("Напиши вес числом в граммах, например 350.")
        return

    target_weight = float(value.replace(",", "."))
    if not 1 <= target_weight <= 100_000:
        await message.answer("Укажи вес от 1 до 100 000 граммов.")
        return

    data = await state.get_data()
    try:
        previous_analysis = FoodAnalysis.model_validate(data.get(LAST_ANALYSIS_KEY))
        updated_analysis = rescale_analysis(previous_analysis, target_weight)
    except (TypeError, ValueError):
        updated_analysis = None

    if updated_analysis is None:
        await reset_user_flow(state, _user_id(message))
        await message.answer(
            "Не получилось надёжно пересчитать порцию по этому анализу.\n\n"
            "Пришли новую фотографию — разберёмся ещё раз.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.set_state(None)
    result_message = await message.answer(
        format_food_result(updated_analysis), reply_markup=analysis_keyboard()
    )
    await _store_result(state, result_message, updated_analysis, _user_id(message), message.chat.id)
