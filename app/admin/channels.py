from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.admin.keyboards import sources_keyboard, source_actions_keyboard
from app.config import is_admin
from app.database import session_scope
from app.models import Source
from app.userbot import sources as source_service


router = Router()


async def show_sources(callback: CallbackQuery):
    items = await source_service.list_sources()
    text = "📡 Каналы и группы" if items else "Источники пока не обнаружены."
    await callback.message.edit_text(text, reply_markup=sources_keyboard(items))


@router.callback_query(F.data == "sources:list")
async def sources_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await show_sources(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("source:view:"))
async def source_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_id)
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
        source.enabled = not source.enabled
        telegram_id = source.telegram_id
        await db.commit()
    await source_service.set_source_enabled(telegram_id, source.enabled)
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
        source.excluded = new_state
        telegram_id = source.telegram_id
        await db.commit()
    await source_service.set_source_excluded(telegram_id, new_state)
    await callback.answer("Готово")
    await source_view(callback)


@router.callback_query(F.data.startswith("source:refresh:"))
async def source_refresh(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from app.userbot.client import create_telethon_client

    source_db_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        source = await db.get(Source, source_db_id)
        if source is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        telegram_id = source.telegram_id
    client = create_telethon_client()
    await client.connect()
    try:
        audience = await source_service.get_source_audience(client, telegram_id)
        source_service.invalidate_source_cache(telegram_id)
    finally:
        await client.disconnect()
    await callback.answer(
        f"Аудитория: {audience if audience is not None else 'неизвестна'}"
    )
    await source_view(callback)
