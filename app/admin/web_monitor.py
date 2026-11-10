from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.admin.keyboards import (
    web_confirm_delete_keyboard,
    web_main_keyboard,
    web_watch_actions_keyboard,
    web_watches_keyboard,
)
from app.config import get_settings, is_admin
from app.database import session_scope
from app.models import WebDetection, WebWatch
from app.services.web_monitor.matcher import validate_watch_query
from app.services.web_monitor.scheduler import get_web_monitor
from app.services.web_monitor.storage import (
    find_watch_by_query,
    get_recent_articles,
    get_watch,
    get_web_counts,
)

router = Router()


class WebMonitorStates(StatesGroup):
    waiting_for_watch = State()
    waiting_for_edit = State()


def _parse_watch_input(value: str) -> tuple[str, str | None]:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    query = ""
    clarification = None
    for line in lines:
        lower = line.casefold()
        if lower.startswith("имя:"):
            query = line.split(":", 1)[1].strip()
        elif lower.startswith("уточнение:"):
            clarification = line.split(":", 1)[1].strip() or None
        elif not query:
            query = line
    return validate_watch_query(query), clarification


async def _render_main() -> tuple[str, object]:
    settings = get_settings()
    watch_count, article_count, _ = await get_web_counts()
    monitor = get_web_monitor()
    status = "🟢 Работает" if settings.web_monitor_enabled else "🔴 Выключен"
    last = monitor.last_check_at.strftime("%H:%M:%S") if monitor.last_check_at else "ещё не выполнялась"
    text = (
        "🌐 Мониторинг интернета\n\n"
        f"Статус: {status}\n"
        f"Интервал планировщика: {settings.web_monitor_interval} секунд\n\n"
        f"Отслеживаемых запросов: {watch_count}\n"
        f"Найдено публикаций: {article_count}\n"
        f"Последняя проверка: {last}"
    )
    return text, web_main_keyboard()


async def _show_main(target):
    text, keyboard = await _render_main()
    await target.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data == "web:list")
async def web_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await _show_main(callback.message)
    await callback.answer()


@router.callback_query(F.data == "web:add")
async def web_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(WebMonitorStates.waiting_for_watch)
    await callback.message.edit_text(
        "Введите имя и фамилию человека:\n\n"
        "Например:\nИван Иванов\n\n"
        "Необязательно можно добавить уточнение строкой:\nУточнение: Москва"
    )
    await callback.answer()


async def _save_watch(message: Message, state: FSMContext, watch_id: int | None = None) -> None:
    try:
        query, clarification = _parse_watch_input(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    existing = await find_watch_by_query(query, clarification)
    if existing is not None and existing.id != watch_id:
        await message.answer("Такой запрос уже отслеживается.")
        return
    async with session_scope() as db:
        if watch_id is None:
            db.add(WebWatch(query=query, clarification=clarification, enabled=True))
        else:
            watch = await db.get(WebWatch, watch_id)
            if watch is None:
                await message.answer("Запрос не найден.")
                await state.clear()
                return
            watch.query = query
            watch.clarification = clarification
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ {'Добавлено' if watch_id is None else 'Изменено'}\n\n{query}"
        + (f"\nУточнение: {clarification}" if clarification else "")
        + "\n\nМониторинг активен."
    )


@router.message(WebMonitorStates.waiting_for_watch)
async def process_watch(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await _save_watch(message, state)


async def _show_watches(target):
    async with session_scope() as db:
        result = await db.execute(select(WebWatch).order_by(WebWatch.id))
        watches = list(result.scalars().all())
        for watch in watches:
            db.expunge(watch)
    await target.edit_text("📋 Список отслеживания", reply_markup=web_watches_keyboard(watches))


@router.callback_query(F.data == "web:watches")
async def web_watches(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await _show_watches(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("web:watch:"))
async def web_watch_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    watch_id = int(callback.data.rsplit(":", 1)[-1])
    watch = await get_watch(watch_id)
    if watch is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    async with session_scope() as db:
        count = await db.scalar(select(func.count(WebDetection.id)).where(WebDetection.watch_id == watch.id)) or 0
    clarification = f"\nУточнение: {escape(watch.clarification)}" if watch.clarification else ""
    text = (
        f"👤 {escape(watch.query)}{clarification}\n\n"
        f"Статус: {'🟢 Активен' if watch.enabled else '🔴 Выключен'}\n"
        f"Найдено публикаций: {count}"
    )
    await callback.message.edit_text(text, reply_markup=web_watch_actions_keyboard(watch.id, watch.enabled))
    await callback.answer()


@router.callback_query(F.data.startswith("web:toggle:"))
async def web_watch_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    watch_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        watch = await db.get(WebWatch, watch_id)
        if watch is None:
            await callback.answer("Не найдено", show_alert=True)
            return
        watch.enabled = not watch.enabled
        await db.commit()
    await callback.answer("Готово")
    await web_watch_view(callback)


@router.callback_query(F.data.startswith("web:edit:"))
async def web_watch_edit(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    watch_id = int(callback.data.rsplit(":", 1)[-1])
    watch = await get_watch(watch_id)
    if watch is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    await state.set_state(WebMonitorStates.waiting_for_edit)
    await state.update_data(watch_id=watch_id)
    await callback.message.edit_text(
        f"Текущее значение: {watch.query}\nВведите новое имя и фамилию:"
    )
    await callback.answer()


@router.message(WebMonitorStates.waiting_for_edit)
async def process_watch_edit(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    await _save_watch(message, state, int(data["watch_id"]))


@router.callback_query(F.data.startswith("web:delete:"))
async def web_watch_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    watch_id = int(callback.data.rsplit(":", 1)[-1])
    await callback.message.edit_text("Удалить запрос?", reply_markup=web_confirm_delete_keyboard(watch_id))
    await callback.answer()


@router.callback_query(F.data.startswith("web:delete_confirm:"))
async def web_watch_delete_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    watch_id = int(callback.data.rsplit(":", 1)[-1])
    async with session_scope() as db:
        watch = await db.get(WebWatch, watch_id)
        if watch is not None:
            await db.delete(watch)
            await db.commit()
    await callback.answer("Удалено")
    await _show_watches(callback.message)


@router.callback_query(F.data == "web:articles")
async def web_articles(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    articles = await get_recent_articles(15)
    if not articles:
        text = "📰 Найденные публикации\n\nПубликаций пока нет."
    else:
        lines = ["📰 Найденные публикации", ""]
        for article in articles:
            lines.append(f"• {escape(article.source_name)} — {escape(article.title)}")
            lines.append(escape(article.url))
        text = "\n".join(lines)[:4096]
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="web:list")]]
    )
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "web:sources")
async def web_sources(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    text = (
        "📡 Источники\n\n"
        "Google News RSS Search — подключён\n"
        "GDELT DOC API — подключён\n"
        f"Прямые RSS — {'настроены' if get_settings().web_rss_urls else 'не настроены'}\n"
        "Webhook/WebSub — опционально"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="web:list")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "web:settings")
async def web_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    settings = get_settings()
    text = (
        "⚙️ Настройки мониторинга интернета\n\n"
        f"Состояние: {'включён' if settings.web_monitor_enabled else 'выключен'}\n"
        f"Интервал: {settings.web_monitor_interval} секунд\n"
        f"Timeout HTTP: {settings.web_monitor_timeout} секунд\n"
        f"Параллельных запросов: {settings.web_monitor_max_concurrency}\n"
        f"Окно актуальности: {settings.web_monitor_freshness_hours} часов\n"
        f"Webhook: {'включён' if settings.web_webhook_enabled else 'выключен'}"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="web:list")]]
        ),
    )
    await callback.answer()

