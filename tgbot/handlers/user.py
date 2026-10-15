import logging
import re

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatJoinRequest, Message

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.handlers.handlers_texts import ACCESS_HANDLER
from tgbot.keyboards.inline import (
    access_kb,
    catalog_keyboard,
    invite_keyboard,
    payment_keyboard,
    training_card_keyboard,
)
from tgbot.services.access import AccessService
from tgbot.services.delivery import DeliveryService
from tgbot.services.payment import PaymentService

logger = logging.getLogger(__name__)

user_router = Router()


class PaymentStates(StatesGroup):
    waiting_for_email = State()


@user_router.message(CommandStart())
async def user_start(message: Message):
    try:
        await message.delete()
    except Exception as e:
        logger.debug("Failed to delete message: %s", e)

    await message.answer(
        ACCESS_HANDLER,
        reply_markup=access_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


@user_router.callback_query(F.data == "access_ok")
async def access_ok(callback_query: types.CallbackQuery):
    await callback_query.answer()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        trainings = await training_repo.get_active()

    await callback_query.message.answer(
        "🏋️ Выберите тренировку\n\n"
        "Ознакомьтесь с доступными программами тренировок и выберите подходящую.",
        reply_markup=catalog_keyboard(trainings),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "back_to_catalog")
async def back_to_catalog(callback_query: types.CallbackQuery):
    await callback_query.answer()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        trainings = await training_repo.get_active()

    await callback_query.message.edit_text(
        "🏋️ Выберите тренировку\n\n"
        "Ознакомьтесь с доступными программами тренировок и выберите подходящую.",
        reply_markup=catalog_keyboard(trainings),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data.startswith("training_"))
async def show_training(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()

    training_id = int(callback_query.data.split("_")[1])
    user_id = callback_query.from_user.id

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        access_repo = AccessRepository(session)

        training = await training_repo.get_by_id(training_id)
        if not training:
            await callback_query.message.answer("Тренировка не найдена.")
            return

        has_access = await access_repo.has_active_access(user_id, training_id)

    text = (
        f"🏋️ {training.title}\n\n"
        f"{training.description or 'Описание отсутствует.'}\n\n"
        f"Стоимость: {training.price} ₽"
    )

    await callback_query.message.edit_text(
        text,
        reply_markup=training_card_keyboard(
            training_id, has_access, float(training.price)
        ),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data.startswith("pay_"))
async def start_payment(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()

    training_id = int(callback_query.data.split("_")[1])

    await state.set_state(PaymentStates.waiting_for_email)
    await state.update_data(training_id=training_id)

    await callback_query.message.answer("Введите email для получения электронного чека:")


@user_router.message(PaymentStates.waiting_for_email)
async def process_payment_email(message: Message, state: FSMContext, bot: Bot, config):
    email = (message.text or "").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        await message.answer("Некорректный email. Введите email ещё раз:")
        return

    data = await state.get_data()
    training_id = data.get("training_id")
    await state.clear()

    user_id = message.from_user.id

    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        training_repo = TrainingRepository(session)
        access_repo = AccessRepository(session)
        order_repo = OrderRepository(session)

        # Check if already purchased
        if await access_repo.has_active_access(user_id, training_id):
            await message.answer("Вы уже приобрели эту тренировку!")
            return

        training = await training_repo.get_by_id(training_id)
        if not training:
            await message.answer("Тренировка не найдена.")
            return

        # Get or create user
        user = await user_repo.get_or_create(user_id, message.from_user.username)
        await user_repo.update_email(user.id, email)

        # Create payment
        payment_service = PaymentService(config.yookassa)
        try:
            payment_id, payment_url = await payment_service.create_payment(
                user_id=user.id,
                training_id=training_id,
                email=email,
                amount=training.price,
                currency=training.currency,
                description=training.title,
            )
        except Exception as e:
            logger.exception("Failed to create payment: %s", e)
            await message.answer("Не удалось создать ссылку на оплату. Попробуйте позже.")
            return

        # Create order
        await order_repo.create(
            user_id=user.id,
            training_id=training_id,
            amount=training.price,
            currency=training.currency,
            customer_email=email,
            payment_id=payment_id,
        )

    await message.answer(
        f"Ссылка на оплату тренировки «{training.title}»:",
        reply_markup=payment_keyboard(payment_url),
    )


@user_router.callback_query(F.data.startswith("get_materials_"))
async def get_materials(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()

    training_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

    session_factory = get_session_factory()
    async with session_factory() as session:
        access_repo = AccessRepository(session)
        if not await access_repo.has_active_access(user_id, training_id):
            await callback_query.message.answer("У вас нет доступа к этой тренировке.")
            return

    await callback_query.message.answer(
        "✅ Оплата прошла успешно!\n\n"
        "Вы приобрели:\n"
        "🏋️ Тренировка\n\n"
        "Ниже находятся материалы вашей тренировки."
    )

    # Deliver materials
    session_factory = get_session_factory()
    async with session_factory() as session:
        delivery_repo = DeliveryRepository(session)
        material_repo = MaterialRepository(session)
        training_repo = TrainingRepository(session)

        delivery_service = DeliveryService(bot)
        await delivery_service.deliver_materials(
            user_id, training_id,
            delivery_repo, material_repo, training_repo,
        )


@user_router.callback_query(F.data.startswith("join_group_"))
async def join_group(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()

    training_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

    session_factory = get_session_factory()
    async with session_factory() as session:
        access_repo = AccessRepository(session)
        if not await access_repo.has_active_access(user_id, training_id):
            await callback_query.message.answer(
                "У вас нет доступа к этой группе."
            )
            return

        training_repo = TrainingRepository(session)
        invite_repo = InviteRepository(session)

        access_service = AccessService(bot)
        invite_link = await access_service.create_invite_link(
            user_id, training_id,
            training_repo, invite_repo,
        )

    if invite_link:
        await callback_query.message.answer(
            "🔐 Ваша ссылка на вступление в группу (действует 24 часа):",
            reply_markup=invite_keyboard(invite_link),
        )
    else:
        await callback_query.message.answer(
            "Не удалось создать ссылку. Попробуйте позже или обратитесь к администратору."
        )


@user_router.chat_join_request()
async def handle_join_request(join_request: ChatJoinRequest, bot: Bot):
    user_id = join_request.from_user.id
    chat_id = join_request.chat.id

    session_factory = get_session_factory()
    async with session_factory() as session:
        access_repo = AccessRepository(session)
        training_repo = TrainingRepository(session)

        access_service = AccessService(bot)
        approved = await access_service.handle_join_request(
            user_id, chat_id,
            access_repo, training_repo,
        )

    if approved:
        await join_request.approve()
        await bot.send_message(
            user_id,
            "🎉 Вы были приняты в группу!",
        )
