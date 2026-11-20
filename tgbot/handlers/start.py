from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from tgbot.handlers.photo import reset_user_flow
from tgbot.keyboards.main import main_menu_keyboard

router = Router(name="start")

START_TEXT = (
    "Вика, привет🤗! 🌿\n\n"
    "Рад тебя видеть.\n\n"
    "У меня сегодня интересная задача —\n"
    "разбираться во всём, что оказывается\n"
    "у тебя на столе.\n\n"
    "Присылай фотографию еды, а я расскажу,\n"
    "что удалось распознать, сколько в ней\n"
    "калорий, белков, жиров и углеводов.\n\n"
    "Обещаю обойтись без лекций\n"
    "о правильном питании. 😉\n\n"
    "Ну что, начнём?"
)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else message.chat.id
    await reset_user_flow(state, user_id)
    await message.answer(START_TEXT, reply_markup=main_menu_keyboard())
