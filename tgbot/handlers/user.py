import logging
import re
from aiogram import Router, Dispatcher, F, Bot, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatJoinRequest, Message
from tgbot.handlers.handlers_texts import START_TEXT, ACCESS_HANDLER
from tgbot.keyboards.inline import first_start_keyboard, to_back, access_kb
from tgbot.services.payment import payment_service

logger = logging.getLogger(__name__)

user_router = Router()
dp = Dispatcher()
API_URL = "http://localhost:8000"


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


class PaymentStates(StatesGroup):
    waiting_for_email = State()


def escape_markdown_v2(text: str) -> str:
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f"([{re.escape(escape_chars)}])", r'\\\1', text)


@user_router.message(CommandStart())
async def user_start(message: Message):
    try:
        await message.delete()
    except Exception as e:
        print(f"⚠️ Ошибка удаления сообщения: {e}")

    await message.answer(
        ACCESS_HANDLER,
        reply_markup=access_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@user_router.callback_query(F.data == "access_ok")
async def access_ok(callback_query: types.CallbackQuery):
    await callback_query.answer()

    await callback_query.message.answer(
        START_TEXT,
        reply_markup=first_start_keyboard(),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "paying_for_subscriptions")
async def pay_one_month(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    await state.set_state(PaymentStates.waiting_for_email)
    await callback_query.message.answer("Введите email для получения электронного чека:")


@user_router.message(PaymentStates.waiting_for_email)
async def process_payment_email(message: Message, state: FSMContext, bot: Bot):
    email = (message.text or "").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        await message.answer("Некорректный email. Введите email ещё раз:")
        return

    await state.clear()

    try:
        payment_id, payment_url = await payment_service.create_payment(
            message.from_user.id,
            message.from_user.username,
            email,
        )
    except Exception:
        logger.exception(
            "Не удалось создать ссылку на оплату для пользователя %s",
            message.from_user.id,
        )
        await message.answer("Не удалось создать ссылку на оплату. Попробуйте позже.")
        return

    text = (
        f"<a href=\"{payment_url}\">👉 Нажмите сюда, чтобы перейти к оплате</a>\n\n"
        "После оплаты, в течении минуты, вы получите доступ в закрытый канал🔐"
    )

    payment_message = await bot.send_message(
        message.from_user.id,
        text,
        parse_mode="HTML"
    )

    payment_service.start_payment_monitor(payment_id, message.from_user.id, bot)


@user_router.chat_join_request()
async def approve_paid_user(join_request: ChatJoinRequest, bot: Bot):
    if not payment_service.is_payment_channel(join_request.chat.id):
        return
    await payment_service.approve_join_request(join_request, bot)
