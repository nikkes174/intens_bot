import html
import logging
import re
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatJoinRequest, Message

from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.delivery import DeliveryRepository
from tgbot.db.repositories.invite import InviteRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.scheduled import ScheduledMessageRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.handlers.handlers_texts import (
    ACCESS_HANDLER,
    BUY_TEXT,
    CATALOG_TEXT,
    DISCOUNT_NOT_AVAILABLE_TEXT,
    DISCOUNT_OFFER_TEXT,
    EMPTY_PURCHASES_TEXT,
    FAQ_TEXT,
    MY_PURCHASES_TEXT,
    WELCOME_TEXT,
    WELCOME_TEXT_NEUTRAL,
)
from tgbot.keyboards.inline import (
    access_kb,
    checkout_keyboard,
    discount_offer_keyboard,
    faq_keyboard,
    invite_keyboard,
    main_menu_keyboard,
    owned_product_keyboard,
    payment_keyboard,
    product_card_keyboard,
    purchases_keyboard,
    training_card_keyboard,
)
from tgbot.services.access import AccessService
from tgbot.services.delivery import DeliveryService
from tgbot.services.discounts import check_discount_eligibility
from tgbot.services.payment import PaymentService
from tgbot.services.pricing import format_money, quantize_money

logger = logging.getLogger(__name__)

user_router = Router()


class PaymentStates(StatesGroup):
    waiting_for_email = State()


def _welcome(first_name: str | None) -> str:
    if not first_name or not first_name.strip():
        return WELCOME_TEXT_NEUTRAL
    return WELCOME_TEXT.format(first_name=html.escape(first_name.strip()))


async def _show_menu(message: Message, *, edit: bool = False) -> None:
    if edit:
        await message.edit_text(CATALOG_TEXT, reply_markup=main_menu_keyboard())
    else:
        await message.answer(CATALOG_TEXT, reply_markup=main_menu_keyboard())


async def _get_user_and_owned_ids(session, telegram_id: int, username: str | None):
    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(telegram_id, username)
    access_repo = AccessRepository(session)
    accesses = await access_repo.get_user_accesses(user.id)
    return user, {access.training_id for access in accesses}


def _format_product_card(training) -> str:
    description = html.escape(training.description or "Описание пока не добавлено.")
    title = html.escape(training.title)
    return f"{title}\n\n{description}\n\n💰 Стоимость: {format_money(training.price)} ₽"


async def _show_discount_offer(callback_query: types.CallbackQuery) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user, _ = await _get_user_and_owned_ids(
            session, callback_query.from_user.id, callback_query.from_user.username
        )
        eligibility = await check_discount_eligibility(
            user.id,
            access_repo=AccessRepository(session),
            order_repo=OrderRepository(session),
            training_repo=TrainingRepository(session),
        )

    if not eligibility.available:
        if eligibility.reason == "all_programs_owned":
            text = "У тебя уже есть все три программы 💪"
        else:
            text = DISCOUNT_NOT_AVAILABLE_TEXT
        await callback_query.message.edit_text(
            text,
            reply_markup=main_menu_keyboard(),
        )
        return

    text = DISCOUNT_OFFER_TEXT.format(price=format_money(eligibility.total))
    await callback_query.message.edit_text(
        text,
        reply_markup=discount_offer_keyboard(format_money(eligibility.total)),
    )


async def _resolve_checkout(session, user_id: int, selection: dict):
    training_repo = TrainingRepository(session)
    access_repo = AccessRepository(session)
    owned = {access.training_id for access in await access_repo.get_user_accesses(user_id)}

    if selection.get("kind") == "discount":
        eligibility = await check_discount_eligibility(
            user_id,
            access_repo=access_repo,
            order_repo=OrderRepository(session),
            training_repo=training_repo,
        )
        if not eligibility.available:
            return None
        lines = eligibility.lines
        selected_ids = ",".join(str(line["training_id"]) for line in lines)
        return {
            "anchor_id": lines[0]["training_id"],
            "checkout_key": f"user:{user_id}:discount:{selected_ids}",
            "items": [
                {
                    "training_id": line["training_id"],
                    "item_price": line["item_price"],
                    "is_discounted": True,
                    "description": line["description"],
                }
                for line in lines
            ],
            "amount": eligibility.total,
            "discount_percent": Decimal("20.00"),
            "discount_amount": eligibility.discount_amount,
            "description": "Персональное предложение на оставшиеся программы со скидкой 20%",
        }

    product = await training_repo.get_by_code(selection.get("code", ""))
    if not product or not product.is_active:
        return None
    if product.is_bundle:
        component_ids = await training_repo.get_component_ids(product.id)
        if component_ids and all(component_id in owned for component_id in component_ids):
            return None
    elif product.id in owned:
        return None

    return {
        "anchor_id": product.id,
        "checkout_key": f"user:{user_id}:product:{product.product_code}",
        "items": [
            {
                "training_id": product.id,
                "item_price": quantize_money(product.price),
                "is_discounted": False,
                "description": product.title,
            }
        ],
        "amount": quantize_money(product.price),
        "discount_percent": Decimal("0.00"),
        "discount_amount": Decimal("0.00"),
        "description": product.title,
    }


async def _create_or_resume_payment(
    *, telegram_id: int, username: str | None, email: str, selection: dict, config
) -> tuple[str | None, str | None, str | None]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(telegram_id, username)
        await user_repo.update_email(user.id, email)

        checkout = await _resolve_checkout(session, user.id, selection)
        if not checkout:
            return None, None, None

        order_repo = OrderRepository(session)
        order = await order_repo.get_pending_checkout(checkout["checkout_key"])
        if not order:
            order = await order_repo.create(
                user_id=user.id,
                training_id=checkout["anchor_id"],
                amount=checkout["amount"],
                currency="RUB",
                customer_email=email,
                idempotency_key=str(uuid.uuid4()),
                checkout_key=checkout["checkout_key"],
                discount_percent=checkout["discount_percent"],
                discount_amount=checkout["discount_amount"],
                items=checkout["items"],
            )
        elif order.payment_url:
            training = await TrainingRepository(session).get_by_id(order.training_id)
            return order.payment_url, training.product_code if training else None, None

        persisted_items = await order_repo.get_order_items(order.id)
        receipt_items = []
        for item in persisted_items:
            product = await TrainingRepository(session).get_by_id(item.training_id)
            receipt_items.append(
                {
                    "description": product.title if product else f"Программа #{item.training_id}",
                    "amount": Decimal(item.item_price),
                }
            )

        payment_service = PaymentService(config.yookassa)
        try:
            payment_id, payment_url = await payment_service.create_payment(
                user_id=user.id,
                training_id=order.training_id,
                order_id=order.id,
                email=email,
                amount=order.amount,
                currency=order.currency,
                description=checkout["description"],
                idempotency_key=order.idempotency_key,
                receipt_items=receipt_items,
            )
        except Exception:
            logger.exception("Failed to create YooKassa payment for order %s", order.id)
            return "", None, None

        await order_repo.set_payment_details(order.id, payment_id, payment_url)
        training = await TrainingRepository(session).get_by_id(order.training_id)
        return payment_url, training.product_code if training else None, None


@user_router.message(CommandStart())
async def user_start(message: Message):
    try:
        await message.delete()
    except Exception as error:
        logger.debug("Failed to delete /start message: %s", error)
    await message.answer(
        ACCESS_HANDLER,
        reply_markup=access_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@user_router.callback_query(F.data == "access_ok")
async def access_ok(callback_query: types.CallbackQuery):
    await callback_query.answer()
    text = _welcome(callback_query.from_user.first_name)
    await callback_query.message.edit_text(
        text,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@user_router.callback_query(F.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await _show_menu(callback_query.message, edit=True)


@user_router.callback_query(F.data.startswith("product_"))
async def show_product(callback_query: types.CallbackQuery):
    await callback_query.answer()
    code = callback_query.data.removeprefix("product_")
    session_factory = get_session_factory()
    async with session_factory() as session:
        user, owned_ids = await _get_user_and_owned_ids(
            session, callback_query.from_user.id, callback_query.from_user.username
        )
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_code(code)
        if not training or not training.is_active:
            await callback_query.message.answer("Программа не найдена.")
            return

        if training.is_bundle:
            component_ids = await training_repo.get_component_ids(training.id)
            if component_ids and all(component_id in owned_ids for component_id in component_ids):
                await callback_query.message.edit_text(
                    "У тебя уже есть все три программы 💪",
                    reply_markup=purchases_keyboard(
                        [
                            item
                            for item in await training_repo.get_products()
                            if item.product_code != "bundle_all" and item.id in owned_ids
                        ]
                    ),
                )
                return
            is_purchased = False
        else:
            is_purchased = training.id in owned_ids

        if not is_purchased:
            scheduled_repo = ScheduledMessageRepository(session)
            await scheduled_repo.schedule(
                dedupe_key=f"user:{user.id}:card-view:{training.id}",
                user_id=user.id,
                training_id=training.id,
                message_type="card_view_24h",
                scheduled_at=datetime.utcnow() + timedelta(hours=24),
            )

    card_text = _format_product_card(training)
    await callback_query.message.edit_text(
        card_text,
        reply_markup=product_card_keyboard(training, is_purchased),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "discount_offer")
async def discount_offer(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await _show_discount_offer(callback_query)


@user_router.callback_query(F.data == "buy_discount_offer")
async def buy_discount_offer(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    session_factory = get_session_factory()
    async with session_factory() as session:
        user, _ = await _get_user_and_owned_ids(
            session, callback_query.from_user.id, callback_query.from_user.username
        )
        selection = {"kind": "discount"}
        checkout = await _resolve_checkout(session, user.id, selection)
        has_email = bool(user.email)

    if not checkout:
        await callback_query.message.edit_text(
            DISCOUNT_NOT_AVAILABLE_TEXT,
            reply_markup=main_menu_keyboard(),
        )
        return

    if not has_email:
        await state.set_state(PaymentStates.waiting_for_email)
        await state.update_data(selection=selection)
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("Введи email для электронного чека:")
        return

    await _show_checkout_summary(
        callback_query.message,
        telegram_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        selection=selection,
        edit=True,
    )


@user_router.callback_query(F.data.startswith("buy_"))
async def show_checkout(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    code = callback_query.data.removeprefix("buy_")
    session_factory = get_session_factory()
    async with session_factory() as session:
        user, owned_ids = await _get_user_and_owned_ids(
            session, callback_query.from_user.id, callback_query.from_user.username
        )
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_code(code)
        if not training or not training.is_active:
            await callback_query.message.answer("Программа не найдена.")
            return
        if training.is_bundle:
            components = await training_repo.get_component_ids(training.id)
            if components and all(component_id in owned_ids for component_id in components):
                await callback_query.message.edit_text(
                    "У тебя уже есть все три программы 💪",
                    reply_markup=purchases_keyboard(
                        [
                            item
                            for item in await training_repo.get_products()
                            if item.id in owned_ids and not item.is_bundle
                        ]
                    ),
                )
                return
        elif training.id in owned_ids:
            card_text = _format_product_card(training)
            await callback_query.message.edit_text(
                card_text,
                reply_markup=product_card_keyboard(training, True),
                parse_mode="HTML",
            )
            return

        has_email = bool(user.email)

    selection = {"kind": "product", "code": code}
    if not has_email:
        await state.set_state(PaymentStates.waiting_for_email)
        await state.update_data(selection=selection)
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("Введи email для электронного чека:")
        return

    await _show_checkout_summary(
        callback_query.message,
        telegram_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        selection=selection,
        edit=True,
    )


@user_router.callback_query(F.data.startswith("checkout_"))
async def checkout(callback_query: types.CallbackQuery, state: FSMContext, config):
    await callback_query.answer()
    key = callback_query.data.removeprefix("checkout_")
    selection = (
        {"kind": "discount"} if key == "discount_offer" else {"kind": "product", "code": key}
    )
    await _begin_checkout(callback_query, selection, state=state, config=config)


async def _begin_checkout(
    callback_query, selection: dict, state: FSMContext | None = None, config=None
):
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(callback_query.from_user.id)
        email = user.email if user else None

    if not email:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        if state:
            await state.set_state(PaymentStates.waiting_for_email)
            await state.update_data(selection=selection)
        await callback_query.message.answer("Введи email для электронного чека:")
        return

    await _send_payment_link(
        callback_query.message,
        telegram_id=callback_query.from_user.id,
        username=callback_query.from_user.username,
        email=email,
        selection=selection,
        config=config,
    )


async def _show_checkout_summary(
    message: Message,
    *,
    telegram_id: int,
    username: str | None,
    selection: dict,
    email: str | None = None,
    edit: bool = False,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_or_create(telegram_id, username)
        if email:
            await user_repo.update_email(user.id, email)
        checkout = await _resolve_checkout(session, user.id, selection)
        if not checkout:
            text = "У тебя уже есть доступ к этим программам. Открой «Мои покупки» 💪"
            if edit:
                await message.edit_text(text, reply_markup=main_menu_keyboard())
            else:
                await message.answer(text)
            return

        existing = await OrderRepository(session).get_pending_checkout(checkout["checkout_key"])
        amount = existing.amount if existing else checkout["amount"]
        if selection.get("kind") == "discount":
            product_name = "оставшиеся программы со скидкой 20%"
            back_callback = "main_menu"
            product_code = "discount_offer"
        else:
            product = await TrainingRepository(session).get_by_code(selection["code"])
            product_name = html.escape(product.title if product else "программа")
            back_callback = f"product_{selection['code']}"
            product_code = selection["code"]

    text = BUY_TEXT.format(product_name=product_name, price=format_money(amount))
    markup = checkout_keyboard(product_code, back_callback=back_callback)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@user_router.message(PaymentStates.waiting_for_email)
async def process_payment_email(message: Message, state: FSMContext):
    email = (message.text or "").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        await message.answer("Некорректный email. Введи email ещё раз:")
        return

    data = await state.get_data()
    selection = data.get("selection")
    if not selection:
        await state.clear()
        await message.answer("Оформление покупки завершено. Выбери программу в меню.")
        return
    await state.clear()
    await _show_checkout_summary(
        message,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        selection=selection,
        email=email,
    )


async def _send_payment_link(
    message: Message,
    *,
    telegram_id: int,
    username: str | None,
    email: str,
    selection: dict,
    config,
) -> None:
    payment_url, product_code, _ = await _create_or_resume_payment(
        telegram_id=telegram_id,
        username=username,
        email=email,
        selection=selection,
        config=config,
    )
    if payment_url is None:
        await message.answer(
            "Кажется, у тебя уже есть доступ к этим программам. Открой «Мои покупки» 💪"
        )
        return
    if not payment_url:
        await message.answer("Не удалось создать ссылку на оплату. Попробуй ещё раз позже.")
        return
    back = f"product_{product_code}" if product_code else "main_menu"
    await message.answer(
        "Ссылка на оплату готова 👇", reply_markup=payment_keyboard(payment_url, back)
    )


@user_router.callback_query(F.data.startswith("get_materials_"))
async def get_materials(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()
    training_id = int(callback_query.data.removeprefix("get_materials_"))
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(callback_query.from_user.id)
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_id(training_id)
        if not user or not training:
            await callback_query.message.answer("Программа не найдена.")
            return

        access_repo = AccessRepository(session)
        component_ids = await training_repo.get_component_ids(training_id)
        has_access = await access_repo.has_active_access(user.id, training_id)
        if training.is_bundle:
            has_access = has_access or (
                bool(component_ids)
                and all(
                    [
                        await access_repo.has_active_access(user.id, item_id)
                        for item_id in component_ids
                    ]
                )
            )
        elif not has_access:
            await callback_query.message.answer("У тебя нет доступа к этой программе.")
            return
        if not has_access:
            await callback_query.message.answer("У тебя нет доступа к этой программе.")
            return

        material_repo = MaterialRepository(session)
        delivery_repo = DeliveryRepository(session)
        product_ids = component_ids if training.is_bundle else [training_id]
        available = False
        for product_id in product_ids:
            materials = await material_repo.get_by_training(product_id)
            if not materials:
                continue
            available = True
            delivery_service = DeliveryService(bot)
            await delivery_service.deliver_materials(
                user.id,
                product_id,
                delivery_repo,
                material_repo,
                training_repo,
                force_resend=True,
            )

    if not available:
        await callback_query.message.answer(
            "Материалы по этой программе пока не загружены. Твой доступ сохранён. "
            "Напиши @annkrasko, и администратор проверит выдачу."
        )


@user_router.callback_query(F.data.startswith("join_group_"))
async def join_group(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()
    training_id = int(callback_query.data.removeprefix("join_group_"))
    session_factory = get_session_factory()
    links = []
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(callback_query.from_user.id)
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_id(training_id)
        if not user or not training:
            await callback_query.message.answer("Программа не найдена.")
            return

        component_ids = await training_repo.get_component_ids(training_id)
        access_repo = AccessRepository(session)
        if training.is_bundle:
            product_ids = component_ids
        else:
            product_ids = [training_id]
        access_service = AccessService(bot)
        invite_repo = InviteRepository(session)
        for product_id in product_ids:
            if not await access_repo.has_active_access(user.id, product_id):
                continue
            invite = await access_service.create_invite_link(
                user.id,
                product_id,
                training_repo,
                invite_repo,
                force_new=True,
            )
            if invite:
                product = await training_repo.get_by_id(product_id)
                links.append((product.title if product else "Программа", invite))

    if not links:
        await callback_query.message.answer(
            "Канал пока не подключён. Напиши @annkrasko, и я помогу разобраться."
        )
        return
    for title, link in links:
        await callback_query.message.answer(
            f"🎥 Канал: {html.escape(title)}",
            reply_markup=invite_keyboard(link),
            parse_mode="HTML",
        )


@user_router.callback_query(F.data == "my_purchases")
async def my_purchases(callback_query: types.CallbackQuery):
    await callback_query.answer()
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(callback_query.from_user.id)
        training_repo = TrainingRepository(session)
        products = await training_repo.get_all()
        if user:
            access_repo = AccessRepository(session)
            owned_ids = {
                access.training_id for access in await access_repo.get_user_accesses(user.id)
            }
            purchases = [
                item
                for item in products
                if item.id in owned_ids and (not item.product_code or not item.is_bundle)
            ]
        else:
            purchases = []

    if not purchases:
        await callback_query.message.edit_text(
            EMPTY_PURCHASES_TEXT,
            reply_markup=purchases_keyboard([]),
        )
        return
    await callback_query.message.edit_text(
        MY_PURCHASES_TEXT,
        reply_markup=purchases_keyboard(purchases),
    )


@user_router.callback_query(F.data.startswith("my_product_"))
async def show_owned_product(callback_query: types.CallbackQuery):
    await callback_query.answer()
    training_id = int(callback_query.data.removeprefix("my_product_"))
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(callback_query.from_user.id)
        access_repo = AccessRepository(session)
        training = await TrainingRepository(session).get_by_id(training_id)
        if (
            not user
            or not training
            or training.is_bundle
            or not await access_repo.has_active_access(user.id, training_id)
        ):
            await callback_query.message.answer("Эта покупка недоступна для твоего аккаунта.")
            return
    await callback_query.message.edit_text(
        html.escape(training.title),
        reply_markup=owned_product_keyboard(training_id),
        parse_mode="HTML",
    )


@user_router.callback_query(F.data == "faq")
async def faq(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(FAQ_TEXT, reply_markup=faq_keyboard())


@user_router.callback_query(F.data.startswith("training_"))
async def legacy_training(callback_query: types.CallbackQuery):
    """Continue to support old links to purchases created before product codes existed."""
    await callback_query.answer()
    training_id = int(callback_query.data.removeprefix("training_"))
    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_id(training_id)
        user = await UserRepository(session).get_by_telegram_id(callback_query.from_user.id)
        has_access = bool(
            user and await AccessRepository(session).has_active_access(user.id, training_id)
        )
    if not training:
        await callback_query.message.answer("Программа не найдена.")
        return
    if not training.product_code and not has_access:
        await callback_query.message.answer(
            "Эта программа больше не продаётся. Открой меню, чтобы выбрать актуальную программу."
        )
        return
    await callback_query.message.edit_text(
        f"{html.escape(training.title)}\n\n{html.escape(training.description or '')}"
        + (f"\n\n💰 Стоимость: {format_money(training.price)} ₽" if not has_access else ""),
        reply_markup=(
            training_card_keyboard(training.id, True, float(training.price))
            if has_access
            else main_menu_keyboard()
        ),
        parse_mode="HTML",
    )


@user_router.chat_join_request()
async def handle_join_request(join_request: ChatJoinRequest, bot: Bot):
    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(join_request.from_user.id)
        if not user:
            return
        access_repo = AccessRepository(session)
        training_repo = TrainingRepository(session)
        access_service = AccessService(bot)
        approved = await access_service.handle_join_request(
            user.id,
            join_request.chat.id,
            access_repo,
            training_repo,
        )

    if approved:
        await join_request.approve()
        await bot.send_message(join_request.from_user.id, "🎉 Твоя заявка одобрена!")
