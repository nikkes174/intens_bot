from __future__ import annotations
from typing import Optional

import math
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.admin.keyboards import (
    mentions_keyboard,
    mention_actions_keyboard,
    confirm_delete_keyboard,
)
from app.config import is_admin
from app.database import session_scope
from app.models import DetectionMention, Mention
from app.services.matcher import normalize


class MentionStates(StatesGroup):
    waiting_for_new_keyword = State()
    waiting_for_edit_keyword = State()


router = Router()
PAGE_SIZE = 10


async def _count_detections(mention_id: int) -> int:
    async with session_scope() as db:
        result = await db.execute(
            select(DetectionMention).where(DetectionMention.mention_id == mention_id)
        )
        return len(result.scalars().all())


async def _keyword_exists(keyword: str, exclude_id: Optional[int] = None) -> bool:
    async with session_scope() as db:
        result = await db.execute(select(Mention))
        target = normalize(keyword)
        for mention in result.scalars().all():
            if exclude_id is not None and mention.id == exclude_id:
                continue
            if normalize(mention.keyword) == target:
                return True
        return False


async def show_mentions(callback_or_message, page: int = 0):
    async with session_scope() as db:
        result = await db.execute(select(Mention).order_by(Mention.id))
        items = list(result.scalars().all())
        for item in items:
            db.expunge(item)

    total_pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = items[start : start + PAGE_SIZE]
    keyboard = mentions_keyboard(page_items, page, total_pages)
    text = "🔑 Упоминания" if items else "Упоминаний пока нет."
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=keyboard)
    else:
        await callback_or_message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "mentions:list")
async def mentions_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.clear()
    await show_mentions(callback, 0)
    await callback.answer()


@router.callback_query(F.data.regexp(r"mentions:list:\d+"))
async def mentions_page(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.clear()
    page = int(callback.data.rsplit(":", 1)[-1])
    await show_mentions(callback, page)
    await callback.answer()


@router.callback_query(F.data.startswith("mention:view:"))
async def mention_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mention_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        mention = await db.get(Mention, mention_id)
        if mention is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        db.expunge(mention)
    count = await _count_detections(mention.id)
    status = "🟢 Активно" if mention.enabled else "🔴 Отключено"
    text = f"🔑 Упоминание: {mention.keyword}\n\nСтатус: {status}\nНайдено публикаций: {count}"
    await callback.message.edit_text(text, reply_markup=mention_actions_keyboard(mention_id))
    await callback.answer()


@router.callback_query(F.data == "mention:add")
async def mention_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(MentionStates.waiting_for_new_keyword)
    await callback.message.edit_text("Введите новое упоминание:")
    await callback.answer()


@router.message(MentionStates.waiting_for_new_keyword)
async def process_new_keyword(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    keyword = (message.text or "").strip()
    if not keyword or len(keyword) > 255:
        await message.answer("Некорректное упоминание.")
        return
    if await _keyword_exists(keyword):
        await message.answer("Такое упоминание уже существует.")
        return
    async with session_scope() as db:
        mention = Mention(keyword=keyword, enabled=True)
        db.add(mention)
        await db.commit()
        await db.refresh(mention)
        mention_id = mention.id
    await state.clear()
    await message.answer(
        f"Упоминание «{keyword}» добавлено.",
        reply_markup=mention_actions_keyboard(mention_id),
    )


@router.callback_query(F.data.startswith("mention:edit:"))
async def mention_edit(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mention_id = int(callback.data.rsplit(":", 1)[-1])
    await state.set_state(MentionStates.waiting_for_edit_keyword)
    await state.update_data(mention_id=mention_id)
    await callback.message.edit_text("Введите новое значение упоминания:")
    await callback.answer()


@router.message(MentionStates.waiting_for_edit_keyword)
async def process_edit_keyword(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    mention_id = data.get("mention_id")
    keyword = (message.text or "").strip()
    if not keyword or len(keyword) > 255:
        await message.answer("Некорректное упоминание.")
        return
    if await _keyword_exists(keyword, exclude_id=mention_id):
        await message.answer("Такое упоминание уже существует.")
        return
    async with session_scope() as db:
        mention = await db.get(Mention, mention_id)
        if mention is None:
            await message.answer("Упоминание не найдено.")
            await state.clear()
            return
        mention.keyword = keyword
        await db.commit()
    await state.clear()
    await message.answer(
        f"Упоминание изменено на «{keyword}».",
        reply_markup=mention_actions_keyboard(mention_id),
    )


@router.callback_query(F.data.startswith("mention:toggle:"))
async def mention_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mention_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        mention = await db.get(Mention, mention_id)
        if mention is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        mention.enabled = not mention.enabled
        await db.commit()
        new_status = mention.enabled
    text = "Включено" if new_status else "Отключено"
    await callback.answer(text)
    await mention_view(callback)


@router.callback_query(F.data.startswith("mention:delete:"))
async def mention_delete_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mention_id = int(callback.data.rsplit(":", 1)[-1])
    await callback.message.edit_text(
        "Удалить упоминание?",
        reply_markup=confirm_delete_keyboard(mention_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mention:delete_confirm:"))
async def mention_delete_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    mention_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        mention = await db.get(Mention, mention_id)
        if mention is not None:
            await db.delete(mention)
            await db.commit()
    await callback.answer("Удалено")
    await show_mentions(callback, 0)
