from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.admin.keyboards import settings_keyboard
from app.config import get_settings, is_admin
from app.services.monitor import get_setting, monitoring_enabled, set_setting


router = Router()


@router.callback_query(F.data == "settings:show")
async def settings_show(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mode = await get_setting("source_mode", get_settings().source_mode)
    tz = await get_setting("timezone", get_settings().timezone)
    status = "включён" if await monitoring_enabled() else "выключен"
    text = (
        "⚙️ Настройки\n\n"
        f"Мониторинг: {status}\n"
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
