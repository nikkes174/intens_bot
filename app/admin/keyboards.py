from __future__ import annotations
from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Упоминания", callback_data="mentions:list")],
            [InlineKeyboardButton(text="📡 Каналы и группы", callback_data="sources:list")],
            [InlineKeyboardButton(text="🌐 Мониторинг интернета", callback_data="web:list")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats:show")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings:show")],
        ]
    )


def web_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить имя", callback_data="web:add")],
            [InlineKeyboardButton(text="📋 Список отслеживания", callback_data="web:watches")],
            [InlineKeyboardButton(text="📰 Найденные публикации", callback_data="web:articles")],
            [InlineKeyboardButton(text="📰 Российские СМИ", callback_data="web:russian_sources")],
            [InlineKeyboardButton(text="📡 Источники", callback_data="web:sources")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="web:settings")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
        ]
    )


def web_russian_sources_keyboard(items) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        source = item["source"]
        if not item["enabled"]:
            status = "⚪"
        elif item["status"] == "ok":
            status = "🟢"
        elif item["status"] in {"error", "unavailable"}:
            status = "🔴"
        else:
            status = "🟡"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {source.name}",
                    callback_data=f"web:ru_source:{source.domain}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="web:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def web_watches_keyboard(items) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        status = "🟢" if item.enabled else "🔴"
        rows.append(
            [InlineKeyboardButton(text=f"{status} {item.query}", callback_data=f"web:watch:{item.id}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ Добавить имя", callback_data="web:add")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="web:list")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def web_watch_actions_keyboard(watch_id: int, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"web:edit:{watch_id}")],
            [
                InlineKeyboardButton(
                    text="⏸ Отключить" if enabled else "▶️ Включить",
                    callback_data=f"web:toggle:{watch_id}",
                )
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"web:delete:{watch_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="web:watches")],
        ]
    )


def web_confirm_delete_keyboard(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"web:delete_confirm:{watch_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"web:watch:{watch_id}"),
            ]
        ]
    )


def mentions_keyboard(items, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
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
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"mentions:list:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"mentions:list:{page + 1}"))
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="mention:add")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mention_actions_keyboard(mention_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"mention:edit:{mention_id}")],
            [
                InlineKeyboardButton(
                    text="⏸ Отключить / ▶️ Включить",
                    callback_data=f"mention:toggle:{mention_id}",
                )
            ],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mention:delete:{mention_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mentions:list")],
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
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"mention:view:{mention_id}"),
            ]
        ]
    )


def sources_keyboard(
    items, page: int = 0, total_pages: int = 1, search: str = ""
) -> InlineKeyboardMarkup:
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
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"sources:list:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"sources:list:{page + 1}"))
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить канал", callback_data="sources:add")])
    rows.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="sources:search")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def source_actions_keyboard(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Вкл/выкл", callback_data=f"source:toggle:{source_id}")],
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
            [InlineKeyboardButton(text="◀️ Назад", callback_data="sources:list")],
        ]
    )


def notification_keyboard(
    url: Optional[str], source_id: int, show_exclude: bool = True
) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть публикацию", url=url)])
    if show_exclude:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚫 Исключить источник",
                    callback_data=f"notify:exclude:{source_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_exclude_keyboard(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, исключить",
                    callback_data=f"notify:exclude_confirm:{source_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"notify:exclude_cancel:{source_id}"
                ),
            ]
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
                    text="🔔 Уведомления вкл/выкл",
                    callback_data="settings:toggle_notifications",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📡 Режим источников", callback_data="settings:toggle_mode"
                )
            ],
            [InlineKeyboardButton(text="🕒 Часовой пояс", callback_data="settings:timezone")],
            [InlineKeyboardButton(text="⛔ Стоп-слова", callback_data="stopwords:list")],
            [InlineKeyboardButton(text="👤 Проверка аккаунта", callback_data="settings:account")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu:main")],
        ]
    )


def source_add_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sources:add_cancel")]
        ]
    )


def stopwords_keyboard(items) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        status = "🟢" if item.enabled else "🔴"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {item.word}",
                    callback_data=f"stopword:view:{item.id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Изменить",
                    callback_data=f"stopword:edit:{item.id}",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"stopword:delete:{item.id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="stopword:add")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="settings:show")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
