import html
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from tgbot.config import Config
from tgbot.db.db import get_session_factory
from tgbot.db.repositories.access import AccessRepository
from tgbot.db.repositories.material import MaterialRepository
from tgbot.db.repositories.order import OrderRepository
from tgbot.db.repositories.training import TrainingRepository
from tgbot.db.repositories.user import UserRepository
from tgbot.keyboards.inline import (
    admin_back_keyboard,
    admin_material_actions_keyboard,
    admin_materials_keyboard,
    admin_menu_keyboard,
    admin_training_detail_keyboard,
    admin_trainings_keyboard,
)
from tgbot.services.pricing import format_money, quantize_money

logger = logging.getLogger(__name__)

admin_router = Router()


class AdminStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_price = State()
    waiting_for_chat_id = State()
    waiting_for_material = State()
    waiting_for_material_caption = State()
    waiting_for_user_search = State()


def is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.tg_bot.admin_ids


@admin_router.message(Command("admin"))
async def admin_start(message: Message, config: Config):
    if not is_admin(message.from_user.id, config):
        await message.answer("⛔ У вас нет доступа к административной панели.")
        return

    await message.answer(
        "⚙️ Административная панель",
        reply_markup=admin_menu_keyboard(),
    )


@admin_router.callback_query(F.data == "admin_back")
async def admin_back(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return
    await callback_query.message.edit_text(
        "⚙️ Административная панель",
        reply_markup=admin_menu_keyboard(),
    )


@admin_router.callback_query(F.data == "admin_close")
async def admin_close(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return
    await callback_query.message.delete()


@admin_router.callback_query(F.data == "admin_trainings")
async def admin_trainings(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        trainings = await training_repo.get_products(active_only=False)

    await callback_query.message.edit_text(
        "🏋️ Управление тренировками",
        reply_markup=admin_trainings_keyboard(trainings),
    )


@admin_router.callback_query(F.data.startswith("admin_training_"))
async def admin_training_detail(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[2])

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        training = await training_repo.get_by_id(training_id)
        if not training:
            await callback_query.message.answer("Тренировка не найдена.")
            return

        materials_count = await training_repo.count_materials(training_id)
        components = (
            await training_repo.get_bundle_components(training_id) if training.is_bundle else []
        )

    group_status = "подключена" if training.telegram_chat_id else "не подключена"
    status = "активна" if training.is_active else "скрыта"

    text = (
        f"Название: {training.title}\n"
        f"Стоимость: {training.price} ₽\n"
        f"Материалов: {materials_count}\n"
        f"Группа: {group_status}\n"
        f"Статус: {status}"
    )
    if training.is_bundle:
        component_names = ", ".join(item.title for item in components) or "не настроен"
        text += f"\nСостав комплекта: {component_names}"

    await callback_query.message.edit_text(
        text,
        reply_markup=admin_training_detail_keyboard(
            training_id, training.is_active, is_bundle=training.is_bundle
        ),
    )


@admin_router.callback_query(F.data.startswith("admin_edit_title_"))
async def admin_edit_title(callback_query: types.CallbackQuery, state: FSMContext, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[3])
    await state.set_state(AdminStates.waiting_for_title)
    await state.update_data(training_id=training_id)
    await callback_query.message.answer("Введите новое название тренировки:")


@admin_router.message(AdminStates.waiting_for_title)
async def process_new_title(message: Message, state: FSMContext, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    title = message.text.strip()
    if not title:
        await message.answer("Название не может быть пустым.")
        return

    data = await state.get_data()
    training_id = data.get("training_id")
    await state.clear()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        await training_repo.update_title(training_id, title)

    await message.answer(f"✅ Название изменено на: {title}")


@admin_router.callback_query(F.data.startswith("admin_edit_desc_"))
async def admin_edit_desc(callback_query: types.CallbackQuery, state: FSMContext, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[3])
    await state.set_state(AdminStates.waiting_for_description)
    await state.update_data(training_id=training_id)
    await callback_query.message.answer("Введите новое описание тренировки:")


@admin_router.message(AdminStates.waiting_for_description)
async def process_new_description(message: Message, state: FSMContext, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    description = message.text.strip()
    data = await state.get_data()
    training_id = data.get("training_id")
    await state.clear()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        await training_repo.update_description(training_id, description)

    await message.answer("✅ Описание обновлено.")


@admin_router.callback_query(F.data.startswith("admin_edit_price_"))
async def admin_edit_price(callback_query: types.CallbackQuery, state: FSMContext, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[3])
    await state.set_state(AdminStates.waiting_for_price)
    await state.update_data(training_id=training_id)
    await callback_query.message.answer("Введите новую стоимость тренировки (в рублях):")


@admin_router.message(AdminStates.waiting_for_price)
async def process_new_price(message: Message, state: FSMContext, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    try:
        price = quantize_money(Decimal(message.text.strip().replace(",", ".")))
        if price < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("Некорректная цена. Введите число:")
        return

    data = await state.get_data()
    training_id = data.get("training_id")
    await state.clear()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        await training_repo.update_price(training_id, price)

    await message.answer(f"✅ Стоимость изменена на: {format_money(price)} ₽")


@admin_router.callback_query(F.data.startswith("admin_materials_"))
async def admin_materials(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[2])

    session_factory = get_session_factory()
    async with session_factory() as session:
        training = await TrainingRepository(session).get_by_id(training_id)
        if training and training.is_bundle:
            await callback_query.message.answer(
                "Материалы комплекта хранятся в карточках входящих программ."
            )
            return
        material_repo = MaterialRepository(session)
        materials = await material_repo.get_by_training(training_id)

    await callback_query.message.edit_text(
        f"📂 Материалы тренировки #{training_id}",
        reply_markup=admin_materials_keyboard(materials, training_id),
    )


@admin_router.callback_query(F.data.startswith("admin_add_material_"))
async def admin_add_material(
    callback_query: types.CallbackQuery, state: FSMContext, config: Config
):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[3])
    session_factory = get_session_factory()
    async with session_factory() as session:
        training = await TrainingRepository(session).get_by_id(training_id)
        if not training or training.is_bundle:
            await callback_query.message.answer(
                "Добавляй материалы в карточки отдельных программ, входящих в комплект."
            )
            return
    await state.set_state(AdminStates.waiting_for_material)
    await state.update_data(training_id=training_id)
    await callback_query.message.answer(
        "Отправьте файл или текст для добавления как материала.\n"
        "Поддерживаются: видео, PDF, документы, фото, аудио, текст."
    )


@admin_router.message(AdminStates.waiting_for_material)
async def process_new_material(message: Message, state: FSMContext, bot: Bot, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    data = await state.get_data()
    training_id = data.get("training_id")

    material_type = None
    telegram_file_id = None
    telegram_file_unique_id = None
    file_name = None
    text_content = None

    if message.video:
        material_type = "video"
        telegram_file_id = message.video.file_id
        telegram_file_unique_id = message.video.file_unique_id
        file_name = message.video.file_name
    elif message.document:
        material_type = "document"
        telegram_file_id = message.document.file_id
        telegram_file_unique_id = message.document.file_unique_id
        file_name = message.document.file_name
    elif message.photo:
        material_type = "photo"
        telegram_file_id = message.photo[-1].file_id
        telegram_file_unique_id = message.photo[-1].file_unique_id
    elif message.audio:
        material_type = "audio"
        telegram_file_id = message.audio.file_id
        telegram_file_unique_id = message.audio.file_unique_id
        file_name = message.audio.file_name
    elif message.text:
        material_type = "text"
        text_content = message.text
    else:
        await message.answer("Неподдерживаемый тип материала.")
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        material_repo = MaterialRepository(session)
        max_order = await material_repo.get_max_sort_order(training_id)

        await material_repo.create(
            training_id=training_id,
            material_type=material_type,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            file_name=file_name,
            caption=message.caption,
            text_content=text_content,
            sort_order=max_order + 1,
        )

    await state.clear()
    await message.answer(
        "✅ Материал успешно добавлен!",
        reply_markup=admin_back_keyboard(),
    )


@admin_router.callback_query(F.data.startswith("admin_delete_material_"))
async def admin_delete_material(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    # Show list of materials to delete
    session_factory = get_session_factory()
    async with session_factory():
        # Extract training_id from callback or use state
        await callback_query.message.answer(
            "Выберите материал для удаления из списка материалов тренировки."
        )


@admin_router.callback_query(F.data.startswith("admin_material_"))
async def admin_material_detail(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    material_id = int(callback_query.data.split("_")[2])

    session_factory = get_session_factory()
    async with session_factory() as session:
        material_repo = MaterialRepository(session)
        material = await material_repo.get_by_id(material_id)
        if not material:
            await callback_query.message.answer("Материал не найден.")
            return

    await callback_query.message.edit_text(
        f"Материал: {material.file_name or material.material_type}",
        reply_markup=admin_material_actions_keyboard(material_id, material.training_id),
    )


@admin_router.callback_query(F.data.startswith("admin_delete_mat_"))
async def admin_delete_material_confirm(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    material_id = int(callback_query.data.split("_")[3])

    session_factory = get_session_factory()
    async with session_factory() as session:
        material_repo = MaterialRepository(session)
        deleted = await material_repo.delete(material_id)

    if deleted:
        await callback_query.message.answer("✅ Материал удалён.")
    else:
        await callback_query.message.answer("Материал не найден.")


@admin_router.callback_query(F.data.startswith("admin_link_group_"))
async def admin_link_group(callback_query: types.CallbackQuery, state: FSMContext, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[3])
    session_factory = get_session_factory()
    async with session_factory() as session:
        training = await TrainingRepository(session).get_by_id(training_id)
        if not training or training.is_bundle:
            await callback_query.message.answer(
                "Подключи отдельный канал к каждой программе комплекта."
            )
            return
    await state.set_state(AdminStates.waiting_for_chat_id)
    await state.update_data(training_id=training_id)

    await callback_query.message.answer(
        "1. Создайте приватную группу.\n"
        "2. Добавьте бота администратором.\n"
        "3. Выдайте права на приглашение пользователей.\n"
        "4. Укажите chat_id группы (например, -1001234567890):"
    )


@admin_router.message(AdminStates.waiting_for_chat_id)
async def process_chat_id(message: Message, state: FSMContext, bot: Bot, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("Некорректный chat_id. Введите число:")
        return

    data = await state.get_data()
    training_id = data.get("training_id")
    await state.clear()

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)

        # Check if group is already linked to another training
        existing = await training_repo.get_by_chat_id(chat_id)
        if existing and existing.id != training_id:
            await message.answer(f"❌ Эта группа уже привязана к тренировке «{existing.title}».")
            return

        # Verify bot is admin in the group
        try:
            chat_member = await bot.get_chat_member(chat_id, bot.id)
            if chat_member.status not in ["administrator", "creator"]:
                await message.answer("❌ Бот не является администратором этой группы.")
                return
            if not chat_member.can_invite_users:
                await message.answer("❌ У бота нет прав на приглашение пользователей.")
                return
        except Exception as e:
            await message.answer(f"❌ Ошибка проверки группы: {e}")
            return

        await training_repo.update_chat_id(training_id, chat_id)

    await message.answer("✅ Группа успешно привязана к тренировке.")


@admin_router.callback_query(F.data.startswith("admin_hide_"))
async def admin_hide_training(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[2])

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        await training_repo.set_active(training_id, False)

    await callback_query.message.answer("✅ Тренировка скрыта.")


@admin_router.callback_query(F.data.startswith("admin_show_"))
async def admin_show_training(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    training_id = int(callback_query.data.split("_")[2])

    session_factory = get_session_factory()
    async with session_factory() as session:
        training_repo = TrainingRepository(session)
        await training_repo.set_active(training_id, True)

    await callback_query.message.answer("✅ Тренировка показана.")


@admin_router.callback_query(F.data == "admin_users")
async def admin_users(callback_query: types.CallbackQuery, state: FSMContext, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    await state.set_state(AdminStates.waiting_for_user_search)
    await callback_query.message.answer("Введите Telegram ID пользователя для поиска:")


@admin_router.message(AdminStates.waiting_for_user_search)
async def search_user(message: Message, state: FSMContext, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    try:
        telegram_user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Некорректный ID. Введите число:")
        return

    await state.clear()

    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)
        access_repo = AccessRepository(session)
        training_repo = TrainingRepository(session)

        user = await user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            await message.answer("Пользователь не найден.")
            return

        orders = await order_repo.get_user_orders(user.id)
        accesses = await access_repo.get_user_accesses(user.id)

        total_spent = sum(o.amount for o in orders if o.status == "succeeded")

        text = (
            f"👤 Пользователь: {html.escape(user.username or 'без username')}\n"
            f"🆔 Telegram ID: {user.telegram_user_id}\n"
            f"📧 Email: {html.escape(user.email or 'не указан')}\n"
            f"💰 Потрачено: {total_spent} ₽\n"
            f"📚 Приобретено тренировок: {len(accesses)}\n"
        )

        if orders:
            text += "\n📋 История заказов:\n"
            for order in orders[:10]:
                line_items = await order_repo.get_order_items(order.id)
                products = []
                for item in line_items:
                    product = await training_repo.get_by_id(item.training_id)
                    if product:
                        products.append(html.escape(product.title))
                if not products:
                    product = await training_repo.get_by_id(order.training_id)
                    products.append(html.escape(product.title if product else "программа"))
                discount = (
                    f", скидка {order.discount_percent}%" if order.discount_percent > 0 else ""
                )
                text += (
                    f"  - Заказ #{order.id} ({' + '.join(products)}): {order.amount} ₽ "
                    f"({order.status}{discount})\n"
                )

        await message.answer(text)


@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats(callback_query: types.CallbackQuery, config: Config):
    await callback_query.answer()
    if not is_admin(callback_query.from_user.id, config):
        return

    session_factory = get_session_factory()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)
        training_repo = TrainingRepository(session)

        users = await user_repo.get_all()
        succeeded_orders = await order_repo.get_succeeded_orders()
        canceled_orders = await order_repo.get_canceled_orders()
        total_revenue = await order_repo.get_total_revenue()
        sales_by_training = await order_repo.get_sales_by_training()

    text = (
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"✅ Успешных покупок: {len(succeeded_orders)}\n"
        f"💰 Выручка: {total_revenue} ₽\n"
        f"❌ Отменённых заказов: {len(canceled_orders)}\n"
    )

    if sales_by_training:
        text += "\n📈 Продажи по тренировкам:\n"
        for training_id, count, total in sales_by_training:
            training = await training_repo.get_by_id(training_id)
            title = training.title if training else f"#{training_id}"
            text += f"  - {title}: {count} шт., {total} ₽\n"

    await callback_query.message.edit_text(text, reply_markup=admin_back_keyboard())
