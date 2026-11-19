import logging
import re
from io import BytesIO

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from tgbot.keyboards.main import (
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
from tgbot.services.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)
router = Router(name="photo")

ANALYZING_TEXT = "🔍 Анализирую твою еду..."
PHOTO_REQUEST_TEXT = "📸 Отправь фотографию еды, которую хочешь проверить."
GENERIC_ERROR_TEXT = (
    "⚠️ Не удалось проанализировать фотографию.\n\nПопробуй отправить её ещё раз немного позже."
)
WEIGHT_PATTERN = re.compile(r"^\d+(?:[.,]\d+)?$")


class FoodStates(StatesGroup):
    waiting_for_weight = State()


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


async def _edit_result(message: Message, text: str, reply_markup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup)


async def _handle_image(
    message: Message,
    state: FSMContext,
    bot,
    openrouter: OpenRouterClient,
) -> None:
    analysis_message = await message.answer(ANALYZING_TEXT)
    image_bytes: bytes | None = None
    prepared_image: bytes | None = None

    try:
        image_bytes = await _download_photo(message, bot)
        prepared_image = prepare_image(image_bytes)
        analysis = await analyze_food(prepared_image, openrouter)

        if not analysis.recognized:
            await state.update_data(last_analysis=None)
            await _edit_result(analysis_message, format_no_food_result(), retry_keyboard())
            return

        await state.update_data(last_analysis=analysis.model_dump())
        await _edit_result(analysis_message, format_food_result(analysis), analysis_keyboard())
    except ImageProcessingError as error:
        if str(error) == "Изображение слишком большое":
            text = "⚠️ Фотография слишком большая. Отправь изображение меньшего размера."
        else:
            text = GENERIC_ERROR_TEXT
        logger.info("Image processing failed: %s", error.__class__.__name__)
        await _edit_result(analysis_message, text, retry_keyboard())
    except OpenRouterError as error:
        logger.warning("OpenRouter request failed: %s", error.__class__.__name__)
        await _edit_result(analysis_message, GENERIC_ERROR_TEXT, retry_keyboard())
    except Exception:
        logger.exception("Unexpected food analysis error")
        await _edit_result(analysis_message, GENERIC_ERROR_TEXT, retry_keyboard())
    finally:
        del image_bytes
        del prepared_image


@router.message(F.text == "📸 Распознать еду")
async def ask_for_photo(message: Message) -> None:
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
    await callback.answer()
    await state.set_state(None)
    await state.update_data(last_analysis=None)
    await callback.message.answer(PHOTO_REQUEST_TEXT, reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "refine_weight")
async def refine_weight_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if not data.get("last_analysis"):
        await callback.message.answer(
            "Сначала отправь фотографию еды, чтобы было что пересчитывать.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await state.set_state(FoodStates.waiting_for_weight)
    await callback.message.answer(
        "⚖️ Напиши приблизительный общий вес блюда в граммах.\n\nНапример: 450"
    )


@router.message(FoodStates.waiting_for_weight)
async def weight_handler(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().replace(" ", "")
    if not WEIGHT_PATTERN.fullmatch(value):
        await message.answer("Введи вес числом в граммах, например: 450")
        return

    target_weight = float(value.replace(",", "."))
    if not 1 <= target_weight <= 100_000:
        await message.answer("Укажи вес от 1 до 100 000 граммов.")
        return

    data = await state.get_data()
    try:
        previous_analysis = FoodAnalysis.model_validate(data.get("last_analysis"))
        updated_analysis = rescale_analysis(previous_analysis, target_weight)
    except (TypeError, ValueError):
        updated_analysis = None

    await state.set_state(None)
    if updated_analysis is None:
        await message.answer(
            "⚠️ Не могу корректно пересчитать результат: исходный вес ингредиентов неизвестен.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.update_data(last_analysis=updated_analysis.model_dump())
    await message.answer(format_food_result(updated_analysis), reply_markup=analysis_keyboard())
