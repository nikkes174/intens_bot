from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot.db.models import TrainingModel


def access_kb():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅️‍ПОДТВЕРЖДАЮ✅",
        callback_data="access_ok"
    )
    builder.adjust(1, 1)
    return builder.as_markup()


def catalog_keyboard(trainings: list[TrainingModel]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    emojis = ["🏋️", "💪", "🔥", "🏃", "⚡"]
    for i, training in enumerate(trainings):
        emoji = emojis[i] if i < len(emojis) else "🏋️"
        builder.button(
            text=f"{emoji} {training.title}",
            callback_data=f"training_{training.id}"
        )
    builder.adjust(1)
    return builder.as_markup()


def training_card_keyboard(
    training_id: int, is_purchased: bool, price: float
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_purchased:
        builder.button(
            text="📂 Получить материалы",
            callback_data=f"get_materials_{training_id}",
        )
        builder.button(
            text="🔐 Вступить в группу",
            callback_data=f"join_group_{training_id}",
        )
    else:
        builder.button(
            text=f"💳 Оплатить — {price} ₽",
            callback_data=f"pay_{training_id}",
        )
    builder.button(text="🔙 Назад", callback_data="back_to_catalog")
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
            text=f"{status} {training.title}",
            callback_data=f"admin_training_{training.id}"
        )
    builder.button(text="🔙 Назад", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


def admin_training_detail_keyboard(training_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить название", callback_data=f"admin_edit_title_{training_id}")
    builder.button(text="📝 Изменить описание", callback_data=f"admin_edit_desc_{training_id}")
    builder.button(text="💰 Изменить стоимость", callback_data=f"admin_edit_price_{training_id}")
    builder.button(text="📂 Управление материалами", callback_data=f"admin_materials_{training_id}")
    builder.button(text="🔗 Привязать группу", callback_data=f"admin_link_group_{training_id}")
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
            callback_data=f"admin_material_{material.id}"
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
    builder.button(
        text="Назад 🔙️",
        callback_data="back_to"
    )
    builder.adjust(1)
    return builder.as_markup()


def payment_keyboard(payment_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", url=payment_url)
    builder.adjust(1)
    return builder.as_markup()


def invite_keyboard(invite_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔐 Вступить в группу", url=invite_link)
    builder.adjust(1)
    return builder.as_markup()
