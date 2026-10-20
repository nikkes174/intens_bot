from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 Упоминания",
                    callback_data="mentions:list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 Каналы и группы",
                    callback_data="sources:list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="stats:show",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="settings:show",
                )
            ],
        ]
    )


def mentions_keyboard(
    items, page: int = 0, total_pages: int = 1
) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        status = "🟢" if item.enabled else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {item.keyword}",
                    callback_data=f"mention:view:{item.id}",
                )
            ]
        )
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"mentions:list:{page - 1}",
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"mentions:list:{page + 1}",
                )
            )
        if nav:
            rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить",
                callback_data="mention:add",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mention_actions_keyboard(mention_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"mention:edit:{mention_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏸ Отключить / ▶️ Включить",
                    callback_data=f"mention:toggle:{mention_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"mention:delete:{mention_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="mentions:list",
                )
            ],
        ]
    )


def confirm_delete_keyboard(mention_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"mention:delete_confirm:{mention_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"mention:view:{mention_id}",
                ),
            ]
        ]
    )


def sources_keyboard(items) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        status = "🟢" if item.enabled and not item.excluded else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {item.title or item.telegram_id}",
                    callback_data=f"source:view:{item.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="menu:main",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def source_actions_keyboard(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Вкл/выкл",
                    callback_data=f"source:toggle:{source_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Исключить/вернуть",
                    callback_data=f"source:exclude:{source_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить аудиторию",
                    callback_data=f"source:refresh:{source_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="sources:list",
                )
            ],
        ]
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Мониторинг вкл/выкл",
                    callback_data="settings:toggle_monitoring",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 Режим источников",
                    callback_data="settings:toggle_mode",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="menu:main",
                )
            ],
        ]
    )
