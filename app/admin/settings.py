from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.admin.keyboards import (
    settings_keyboard,
    stopwords_keyboard,
)
from app.config import get_settings, is_admin
from app.database import session_scope
from sqlalchemy import select
from app.models import StopWord
from app.services.monitor import (
    get_setting,
    monitoring_enabled,
    notifications_enabled,
    set_setting,
)
import app.services.monitor as monitor_module
from zoneinfo import ZoneInfo


router = Router()


class SettingsStates(StatesGroup):
    waiting_for_timezone = State()
    waiting_for_stopword = State()


@router.callback_query(F.data == "settings:show")
async def settings_show(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mode = await get_setting("source_mode", get_settings().source_mode)
    tz = await get_setting("timezone", get_settings().timezone)
    mon = "включён" if await monitoring_enabled() else "выключен"
    notif = "включены" if await notifications_enabled() else "выключены"
    text = (
        "⚙️ Настройки\n\n"
        f"Мониторинг: {mon}\n"
        f"Уведомления: {notif}\n"
        f"Режим источников: {mode}\n"
        f"Часовой пояс: {tz}"
    )
    await callback.message.edit_text(text, reply_markup=settings_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings:toggle_monitoring")
async def settings_toggle_monitoring(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    current = await monitoring_enabled()
    await set_setting("monitoring_enabled", "false" if current else "true")
    await callback.answer("Готово")
    await settings_show(callback)


@router.callback_query(F.data == "settings:toggle_notifications")
async def settings_toggle_notifications(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    current = await notifications_enabled()
    await set_setting("notifications_enabled", "false" if current else "true")
    await callback.answer("Готово")
    await settings_show(callback)


@router.callback_query(F.data == "settings:toggle_mode")
async def settings_toggle_mode(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    current = await get_setting("source_mode", get_settings().source_mode)
    new_mode = "selected" if current == "all" else "all"
    await set_setting("source_mode", new_mode)
    await callback.answer("Готово")
    await settings_show(callback)


@router.callback_query(F.data == "settings:timezone")
async def settings_timezone(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_for_timezone)
    await callback.message.edit_text("Введите часовой пояс, например Asia/Bangkok:")
    await callback.answer()


@router.message(SettingsStates.waiting_for_timezone)
async def process_timezone(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    try:
        ZoneInfo(value)
    except Exception:
        await message.answer("Некорректный часовой пояс.")
        return
    await set_setting("timezone", value)
    await state.clear()
    await message.answer(f"Часовой пояс установлен: {value}")


@router.callback_query(F.data == "settings:account")
async def settings_account(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    client = monitor_module._client
    authorized = False
    if client is not None:
        try:
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False
    text = "Пользовательский аккаунт: " + (
        "подключён" if authorized else "не подключён"
    )
    await callback.message.edit_text(text, reply_markup=settings_keyboard())
    await callback.answer()


@router.callback_query(F.data == "stopwords:list")
async def stopwords_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    async with session_scope() as db:
        result = await db.execute(select(StopWord).order_by(StopWord.id))
        items = list(result.scalars().all())
        for item in items:
            db.expunge(item)
    await callback.message.edit_text(
        "⛔ Стоп-слова", reply_markup=stopwords_keyboard(items)
    )
    await callback.answer()


@router.callback_query(F.data == "stopword:add")
async def stopword_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(SettingsStates.waiting_for_stopword)
    await callback.message.edit_text("Введите стоп-слово:")
    await callback.answer()


@router.message(SettingsStates.waiting_for_stopword)
async def process_stopword(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    word = (message.text or "").strip()
    if not word:
        await message.answer("Пустое значение.")
        return
    async with session_scope() as db:
        existing = await db.execute(select(StopWord).where(StopWord.word == word))
        if existing.scalar_one_or_none() is None:
            db.add(StopWord(word=word, enabled=True))
            await db.commit()
    await state.clear()
    await message.answer(f"Стоп-слово добавлено: {word}")


@router.callback_query(F.data.startswith("stopword:view:"))
async def stopword_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    stopword_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        item = await db.get(StopWord, stopword_id)
        if item is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        item.enabled = not item.enabled
        await db.commit()
    await callback.answer("Готово")
    await stopwords_list(callback)
