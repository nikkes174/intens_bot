from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin.keyboards import (
    sources_keyboard,
    source_actions_keyboard,
    confirm_exclude_keyboard,
)
from app.config import is_admin
from app.database import session_scope
from app.models import Source
import app.services.monitor as monitor_module
from app.userbot import sources as source_service

router = Router()
PAGE_SIZE = 12
SOURCE_SEARCH = "source_search"
_original_notification_keyboards = {}


def _strip_exclude_button(markup):
    if markup is None:
        return None
    rows = []
    for row in markup.inline_keyboard:
        if any(
            getattr(button, "callback_data", "").startswith("notify:exclude:")
            for button in row
        ):
            continue
        rows.append(row)
    return type(markup)(inline_keyboard=rows)


class SourceStates(StatesGroup):
    waiting_for_search = State()


async def _show_sources(callback: CallbackQuery, page: int = 0, search: str = ""):
    total = await source_service.count_sources(search or None)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    items = await source_service.list_sources(
        limit=PAGE_SIZE, offset=page * PAGE_SIZE, search=search or None
    )
    text = "📡 Каналы и группы" if items else "Источники не найдены."
    if search:
        text += f"\nПоиск: {search}"
    await callback.message.edit_text(
        text, reply_markup=sources_keyboard(items, page, total_pages, search)
    )


@router.callback_query(F.data == "sources:list")
async def sources_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.clear()
    await _show_sources(callback, 0, "")
    await callback.answer()


@router.callback_query(F.data.regexp(r"sources:list:\d+"))
async def sources_page(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    data = await state.get_data()
    search = data.get(SOURCE_SEARCH, "")
    page = int(callback.data.rsplit(":", 1)[-1])
    await _show_sources(callback, page, search)
    await callback.answer()


@router.callback_query(F.data == "sources:search")
async def sources_search(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(SourceStates.waiting_for_search)
    await callback.message.edit_text("Введите часть названия источника:")
    await callback.answer()


@router.message(SourceStates.waiting_for_search)
async def process_source_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    search = (message.text or "").strip()
    await state.update_data(**{SOURCE_SEARCH: search})
    await state.set_state(None)
    # Reuse a fake callback-like edit by sending new message with keyboard.
    total = await source_service.count_sources(search or None)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    items = await source_service.list_sources(
        limit=PAGE_SIZE, offset=0, search=search or None
    )
    text = "📡 Каналы и группы"
    if search:
        text += f"\nПоиск: {search}"
    if not items:
        text = "Источники не найдены."
    from app.admin.keyboards import sources_keyboard

    await message.answer(
        text, reply_markup=sources_keyboard(items, 0, total_pages, search)
    )


@router.callback_query(F.data.startswith("source:view:"))
async def source_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_db_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_db_id)
        if source is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        db.expunge(source)
    audience = source.audience
    audience_text = (
        f"{audience:,}".replace(",", " ") if audience is not None else "неизвестна"
    )
    text = (
        f"📡 {source.title or source.telegram_id}\n\n"
        f"ID: {source.telegram_id}\n"
        f"Тип: {source.source_type or 'неизвестно'}\n"
        f"Статус: {'🟢 включён' if source.enabled else '🔴 выключен'}\n"
        f"Исключён: {'да' if source.excluded else 'нет'}\n"
        f"Аудитория: {audience_text}"
    )
    await callback.message.edit_text(
        text, reply_markup=source_actions_keyboard(source.id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("source:toggle:"))
async def source_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_db_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_db_id)
        if source is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_state = not source.enabled
        telegram_id = source.telegram_id
    await source_service.set_source_enabled(telegram_id, new_state)
    await callback.answer("Готово")
    await source_view(callback)


@router.callback_query(F.data.startswith("source:exclude:"))
async def source_exclude(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_db_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_db_id)
        if source is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        new_state = not source.excluded
        telegram_id = source.telegram_id
    await source_service.set_source_excluded(telegram_id, new_state)
    await callback.answer("Готово")
    await source_view(callback)


@router.callback_query(F.data.startswith("source:refresh:"))
async def source_refresh(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_db_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_db_id)
        if source is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        telegram_id = source.telegram_id
    service = source_service.get_source_service()
    if monitor_module._client is not None:
        service.client = monitor_module._client
    audience = await service.refresh_audience(telegram_id)
    await callback.answer(
        f"Аудитория: {audience if audience is not None else 'неизвестна'}"
    )
    await source_view(callback)


@router.callback_query(F.data.startswith("notify:exclude:"))
async def notify_exclude_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    key = (callback.message.chat.id, callback.message.message_id)
    _original_notification_keyboards[key] = callback.message.reply_markup
    await callback.message.edit_reply_markup(
        reply_markup=confirm_exclude_keyboard(source_id)
    )
    await callback.answer("Подтвердите исключение источника", show_alert=True)


@router.callback_query(F.data.startswith("notify:exclude_confirm:"))
async def notify_exclude_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    await source_service.set_source_excluded(source_id, True)
    key = (callback.message.chat.id, callback.message.message_id)
    original_markup = _original_notification_keyboards.pop(key, None)
    await callback.message.edit_reply_markup(
        reply_markup=_strip_exclude_button(original_markup)
    )
    await callback.answer("Источник исключён")


@router.callback_query(F.data.startswith("notify:exclude_cancel:"))
async def notify_exclude_cancel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    key = (callback.message.chat.id, callback.message.message_id)
    original_markup = _original_notification_keyboards.pop(key, None)
    if original_markup is None:
        original_markup = callback.message.reply_markup
    await callback.message.edit_reply_markup(reply_markup=original_markup)
    await callback.answer("Отменено")
