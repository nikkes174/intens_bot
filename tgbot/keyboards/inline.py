from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot.db.models import TrainingModel
from tgbot.services.pricing import format_money


def access_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅️‍ПОДТВЕРЖДАЮ✅", callback_data="access_ok")
    builder.adjust(1, 1)
    return builder.as_markup()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🍑 Glute Protocol", callback_data="product_glute")
    builder.button(text="🔙 V-Shape", callback_data="product_vshape")
    builder.button(text="🎯 Core Protocol", callback_data="product_core")
    builder.button(text="💎 Все 3 со скидкой", callback_data="product_bundle_all")
    builder.button(text="📂 Мои покупки", callback_data="my_purchases")
    builder.button(text="❓ Вопросы", callback_data="faq")
    builder.button(text="💬 Написать тренеру", url="https://t.me/annkrasko")
    builder.adjust(1, 1, 1, 1, 2, 1)
    return builder.as_markup()


def product_card_keyboard(training: TrainingModel, is_purchased: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_purchased:
        builder.button(text="📂 Получить материалы", callback_data=f"get_materials_{training.id}")
        builder.button(text="🎥 Перейти в канал", callback_data=f"join_group_{training.id}")
    else:
        builder.button(
            text=f"💳 Купить за {format_money(training.price)} ₽",
            callback_data=f"buy_{training.product_code}",
        )
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def checkout_keyboard(product_code: str, back_callback: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", callback_data=f"checkout_{product_code}")
    builder.button(text="⬅️ Назад", callback_data=back_callback or f"product_{product_code}")
    builder.adjust(1)
    return builder.as_markup()


def payment_keyboard(payment_url: str, back_callback: str = "main_menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", url=payment_url)
    builder.button(text="⬅️ В меню", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def purchases_keyboard(trainings: list[TrainingModel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for training in trainings:
        builder.button(
            text=training.title,
            callback_data=f"my_product_{training.id}",
        )
    if not trainings:
        builder.button(text="🏋️ Выбрать программу", callback_data="main_menu")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def owned_product_keyboard(
    training_id: int, back_callback: str = "my_purchases"
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Получить программу", callback_data=f"get_materials_{training_id}")
    builder.button(text="🎥 Перейти в канал", callback_data=f"join_group_{training_id}")
    builder.button(text="⬅️ Назад", callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()


def faq_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📂 Мои покупки", callback_data="my_purchases")
    builder.button(text="💬 Написать тренеру", url="https://t.me/annkrasko")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def card_reminder_keyboard(product_code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏋️ Посмотреть программу", callback_data=f"product_{product_code}")
    builder.button(text="💬 Задать вопрос", url="https://t.me/annkrasko")
    builder.adjust(1)
    return builder.as_markup()


def followup_3_days_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать Ане", url="https://t.me/annkrasko")
    builder.adjust(1)
    return builder.as_markup()


def followup_7_days_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Посмотреть предложение", callback_data="discount_offer")
    builder.button(text="⬅️ В меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def discount_offer_keyboard(total: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💳 Купить оставшиеся за {total} ₽", callback_data="buy_discount_offer")
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def cancellation_keyboard(product_code: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if product_code:
        builder.button(text="🔄 Попробовать снова", callback_data=f"buy_{product_code}")
    else:
        builder.button(text="🔄 Попробовать снова", callback_data="discount_offer")
    builder.button(text="💬 Написать тренеру", url="https://t.me/annkrasko")
    builder.button(text="⬅️ В меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def catalog_keyboard(trainings: list[TrainingModel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    emojis = ["🏋️", "💪", "🔥", "🏃", "⚡"]
    for i, training in enumerate(trainings):
        emoji = emojis[i] if i < len(emojis) else "🏋️"
        callback = (
            f"product_{training.product_code}"
            if training.product_code
            else f"training_{training.id}"
        )
        builder.button(text=f"{emoji} {training.title}", callback_data=callback)
    builder.adjust(1)
    return builder.as_markup()


def training_card_keyboard(
    training_id: int, is_purchased: bool, price: float
) -> InlineKeyboardMarkup:
    """Legacy keyboard kept for callers and old training records."""
    builder = InlineKeyboardBuilder()
    if is_purchased:
        builder.button(text="📂 Получить материалы", callback_data=f"get_materials_{training_id}")
        builder.button(text="🎥 Перейти в канал", callback_data=f"join_group_{training_id}")
    else:
        builder.button(
            text=f"💳 Купить за {format_money(price)} ₽", callback_data=f"pay_{training_id}"
        )
    builder.button(text="⬅️ Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏋️ Управление тренировками", callback_data="admin_trainings")
    builder.button(text="📂 Материалы тренировок", callback_data="admin_materials")
    builder.button(text="👥 Пользователи и покупки", callback_data="admin_users")
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔙 Закрыть", callback_data="admin_close")
    builder.adjust(1)
    return builder.as_markup()


def admin_trainings_keyboard(trainings: list[TrainingModel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for training in trainings:
        status = "🟢" if training.is_active else "🔴"
        builder.button(
            text=f"{status} {training.title}", callback_data=f"admin_training_{training.id}"
        )
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


def admin_training_detail_keyboard(
    training_id: int,
    is_active: bool,
    is_bundle: bool = False,
    is_env_group: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить название", callback_data=f"admin_edit_title_{training_id}")
    builder.button(text="📝 Изменить описание", callback_data=f"admin_edit_desc_{training_id}")
    builder.button(text="💰 Изменить стоимость", callback_data=f"admin_edit_price_{training_id}")
    if not is_bundle:
        builder.button(
            text="📂 Управление материалами", callback_data=f"admin_materials_{training_id}"
        )
        if not is_env_group:
            builder.button(
                text="🔗 Привязать группу", callback_data=f"admin_link_group_{training_id}"
            )
    if is_active:
        builder.button(text="🔴 Скрыть тренировку", callback_data=f"admin_hide_{training_id}")
    else:
        builder.button(text="🟢 Показать тренировку", callback_data=f"admin_show_{training_id}")
    builder.button(text="🔙 Назад", callback_data="admin_trainings")
    builder.adjust(1)
    return builder.as_markup()


def admin_materials_keyboard(materials: list, training_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, material in enumerate(materials, 1):
        builder.button(
            text=f"{i}. {material.file_name or material.material_type}",
            callback_data=f"admin_material_{material.id}",
        )
    builder.button(text="➕ Добавить материал", callback_data=f"admin_add_material_{training_id}")
    builder.button(text="🗑 Удалить материал", callback_data=f"admin_delete_material_{training_id}")
    builder.button(text="🔀 Изменить порядок", callback_data=f"admin_reorder_{training_id}")
    builder.button(text="🔙 Назад", callback_data=f"admin_training_{training_id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_material_actions_keyboard(material_id: int, training_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"admin_delete_mat_{material_id}")
    builder.button(text="🔙 Назад", callback_data=f"admin_materials_{training_id}")
    builder.adjust(1)
    return builder.as_markup()


def admin_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


def to_back():
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад 🔙️", callback_data="back_to")
    builder.adjust(1)
    return builder.as_markup()


def invite_keyboard(invite_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎥 Перейти в канал", url=invite_link)
    builder.adjust(1)
    return builder.as_markup()
