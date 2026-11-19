from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from tgbot.handlers.photo import reset_user_flow
from tgbot.keyboards.main import main_menu_keyboard

router = Router(name="start")

START_TEXT = (
    "🥗 Привет! Я бот «Что я ем?»\n\n"
    "Хочешь узнать, что находится на твоей тарелке?\n\n"
    "📸 Просто отправь мне фотографию еды.\n\n"
    "Я попробую определить:\n\n"
    "🔥 Калорийность\n"
    "🥩 Белки\n"
    "🥑 Жиры\n"
    "🍞 Углеводы\n\n"
    "А ещё расскажу, из чего состоит блюдо.\n\n"
    "Все значения приблизительные."
)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else message.chat.id
    await reset_user_flow(state, user_id)
    await message.answer(START_TEXT, reply_markup=main_menu_keyboard())
