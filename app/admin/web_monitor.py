from __future__ import annotations

import asyncio
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.admin.keyboards import (
    web_confirm_delete_keyboard,
    web_main_keyboard,
    web_registry_keyboard,
    web_registry_source_keyboard,
    web_registry_values_keyboard,
    web_russian_sources_keyboard,
    web_watch_actions_keyboard,
    web_watches_keyboard,
)
from app.config import get_settings, is_admin
from app.database import session_scope
from app.models import WebDetection, WebWatch
from app.services.web_monitor.matcher import validate_watch_query
from app.services.web_monitor.media_registry import (
    SOURCE_CATEGORIES,
    ensure_candidate,
    get_source,
    get_source_endpoints,
    list_sources,
    registry_regions,
    registry_statistics,
    registry_summary,
)
from app.services.web_monitor.media_registry import (
    set_source_enabled as set_registry_source_enabled,
)
from app.services.web_monitor.russian_sources import (
    get_source_overview,
    set_source_enabled,
)
from app.services.web_monitor.scheduler import get_web_monitor
from app.services.web_monitor.source_autodiscovery import (
    check_registered_sources,
    check_source_now,
    discover_and_register_domain,
)
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
    waiting_for_source = State()
    waiting_for_source_search = State()


def _registry_status_label(source) -> str:
    if not source.enabled:
        return "⚪ Отключён"
    if source.status == "ok":
        return "🟢 Работает"
    if source.status == "error":
        return "🔴 Ошибка"
    if source.status == "aggregator_only":
        return "🔵 Только агрегаторы"
    return "🟡 Не проверен"


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


async def _render_registry(
    *,
    page: int = 0,
    search: str = "",
    region: str | None = None,
    category: str | None = None,
) -> tuple[str, object]:
    settings = get_settings()
    summary = await registry_summary()
    page_size = 12
    items, total = await list_sources(
        page=page,
        page_size=page_size,
        search=search,
        region=region,
        category=category,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    filters = ""
    if search:
        filters += f"\nПоиск: {escape(search)}"
    if region:
        filters += f"\nРегион: {escape(region)}"
    if category:
        filters += f"\nКатегория: {escape(SOURCE_CATEGORIES.get(category, category))}"
    text = (
        "🌐 Источники интернета\n\n"
        f"Всего источников: {summary['total']}\n"
        f"🟢 Работают: {summary['working']}\n"
        f"🟡 Ожидают проверки: {summary['pending']}\n"
        f"🔴 Ошибки: {summary['errors']}\n"
        f"\nRSS/Atom: {summary['rss']}  Sitemap: {summary['sitemaps']}\n"
        f"Федеральные: {summary['category_federal']}  Региональные: {summary['category_regional']}\n"
        f"Городские/районные: {summary['category_local']}  Деловые: {summary['category_business']}\n"
        f"Технологические: {summary['category_technology']}  Отраслевые: {summary['category_industry']}\n"
        f"Автообнаружение: {'включено' if settings.web_source_autodiscovery_enabled else 'выключено'}"
        f"{filters}"
    )
    return text, web_registry_keyboard(page, total_pages, items)


async def _show_registry(target, **filters):
    text, keyboard = await _render_registry(**filters)
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


async def _show_russian_sources(target):
    overview = await get_source_overview()
    total = len(overview)
    active = sum(
        1
        for item in overview
        if item["enabled"]
        and item["source"].rss_url
        and item["status"] == "ok"
    )
    unavailable = sum(
        1
        for item in overview
        if item["enabled"]
        and (
            item["source"].rss_url is None
            or item["status"] == "error"
        )
    )
    lines = [
        "📰 Мониторинг российских СМИ",
        "",
        f"Источников: {total}",
        f"Активных: {active}",
        f"Недоступных: {unavailable}",
        "",
        "Нажмите на издание, чтобы включить или отключить его.",
    ]
    for item in overview:
        source = item["source"]
        lines.append(f"{source.name} — {_russian_source_status(item)}")
    await target.edit_text("\n".join(lines), reply_markup=web_russian_sources_keyboard(overview))


def _russian_source_status(item) -> str:
    source = item["source"]
    if not item["enabled"]:
        return "⚪ Отключён"
    if source.rss_url is None:
        return "Нет прямого RSS"
    if item["status"] == "ok":
        return "🟢 Работает"
    if item["status"] == "error":
        return "🔴 Ошибка"
    return "🟡 Не проверен"


@router.callback_query(F.data == "web:russian_sources")
async def web_russian_sources(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await _show_russian_sources(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("web:ru_source:"))
async def web_russian_source_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    domain = callback.data.rsplit(":", 1)[-1]
    overview = await get_source_overview()
    item = next((item for item in overview if item["source"].domain == domain), None)
    if item is None:
        await callback.answer("Источник не найден", show_alert=True)
        return
    await set_source_enabled(domain, not bool(item["enabled"]))
    await _show_russian_sources(callback.message)
    await callback.answer("Настройка сохранена")


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


@router.callback_query(F.data == "web:registry")
async def web_registry(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await _show_registry(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("web:registry:page:"))
async def web_registry_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    page = max(0, int(callback.data.rsplit(":", 1)[-1]))
    await _show_registry(callback.message, page=page)
    await callback.answer()


@router.callback_query(F.data == "web:registry:categories")
async def web_registry_categories(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    values = [(key, label) for key, label in SOURCE_CATEGORIES.items()]
    await callback.message.edit_text(
        "📂 Категории источников",
        reply_markup=web_registry_values_keyboard("category", values),
    )
    await callback.answer()


@router.callback_query(F.data == "web:registry:regions")
async def web_registry_regions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    regions = await registry_regions()
    values = [(region, region) for region in regions] or [("", "Регионы ещё не указаны")]
    await callback.message.edit_text(
        "🌍 Регионы источников",
        reply_markup=web_registry_values_keyboard("region", values),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("web:registry:filter:"))
async def web_registry_filter(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    _, _, _, kind, value = callback.data.split(":", 4)
    await _show_registry(callback.message, **{kind: value or None})
    await callback.answer()


@router.callback_query(F.data == "web:registry:search")
async def web_registry_search(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(WebMonitorStates.waiting_for_source_search)
    await callback.message.edit_text("Введите название или домен для поиска источника.")
    await callback.answer()


@router.message(WebMonitorStates.waiting_for_source_search)
async def process_registry_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    text, keyboard = await _render_registry(search=(message.text or "").strip())
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("web:registry:source:"))
async def web_registry_source(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    source = await get_source(source_id)
    if source is None:
        await callback.answer("Источник не найден", show_alert=True)
        return
    endpoints = await get_source_endpoints(source_id)
    endpoint_text = "\n".join(
        f"• {endpoint.kind}: {escape(endpoint.url)} — {endpoint.status}"
        for endpoint in endpoints[:20]
    ) or "Нет проверенных способов получения публикаций"
    text = (
        f"🌐 {escape(source.name or source.domain)}\n\n"
        f"Домен: {escape(source.domain)}\n"
        f"Категория: {escape(SOURCE_CATEGORIES.get(source.category, source.category))}\n"
        f"Регион: {escape(source.region or '—')}\n"
        f"Статус: {_registry_status_label(source)}\n"
        f"Ошибка: {escape(source.error or '—')}\n\n"
        f"Методы:\n{endpoint_text}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=web_registry_source_keyboard(source.id, source.enabled),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("web:registry:toggle:"))
async def web_registry_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    source = await get_source(source_id)
    if source is None:
        await callback.answer("Источник не найден", show_alert=True)
        return
    await set_registry_source_enabled(source_id, not source.enabled)
    await web_registry_source(callback)


@router.callback_query(F.data.startswith("web:registry:source_check:"))
async def web_registry_source_check(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    source_id = int(callback.data.rsplit(":", 1)[-1])
    asyncio.create_task(
        check_source_now(get_web_monitor().client, source_id),
        name=f"web-source-check-{source_id}",
    )
    await callback.answer("Проверка запущена")


@router.callback_query(F.data == "web:registry:add")
async def web_registry_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    await state.set_state(WebMonitorStates.waiting_for_source)
    await callback.message.edit_text(
        "Введите домен или URL сайта. Сайт будет проверен без автоматического обхода страниц."
    )
    await callback.answer()


@router.message(WebMonitorStates.waiting_for_source)
async def process_registry_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    value = (message.text or "").strip()
    try:
        registered, result = await discover_and_register_domain(
            get_web_monitor().client,
            value,
            discovered_by="admin",
        )
    except (OSError, PermissionError, ValueError) as exc:
        await message.answer(f"❌ Адрес не прошёл проверку: {escape(str(exc))}")
        return
    await state.clear()
    if registered:
        await message.answer(
            f"✅ Источник подключён\nМетоды: {', '.join(result.methods)}"
        )
    else:
        await ensure_candidate(value, name=value, discovered_by="admin")
        await message.answer(
            "🟡 Источник не подключён автоматически. Он сохранён как кандидат для ручной проверки.\n"
            + ("\n".join(result.errors) or "Рабочий RSS/Atom или sitemap не найден")
        )


@router.callback_query(F.data == "web:registry:check")
async def web_registry_check(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    asyncio.create_task(
        check_registered_sources(
            get_web_monitor().client,
            limit=get_settings().web_source_check_concurrency,
        ),
        name="web-registry-check",
    )
    await callback.answer("Проверка источников запущена")


@router.callback_query(F.data == "web:registry:stats")
async def web_registry_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    summary = await registry_summary()
    statistics = await registry_statistics()
    text = (
        "📊 Статистика интернет-источников\n\n"
        f"Источников: {summary['total']}\n"
        f"Рабочих: {summary['working']}\n"
        f"RSS/Atom: {summary['rss']}\n"
        f"Sitemap: {summary['sitemaps']}\n"
        f"Только агрегаторы: {summary['aggregators']}\n"
        f"Публикаций за сутки: {statistics['publications_24h']}\n"
        f"Полных текстов обработано: {statistics['full_texts_24h']}\n"
        f"Совпадений по ФИО: {statistics['matches_24h']}\n"
        f"Отправлено уведомлений: {statistics['sent_24h']}\n"
        f"Новых источников: {statistics['new_sources_24h']}\n\n"
        f"Средняя задержка обнаружения: "
        f"{statistics['average_detection_time_seconds'] or 'нет данных'} сек.\n"
        "Источники с наибольшим числом совпадений:\n"
        + (
            "\n".join(
                f"• {item['source_name']}: {item['matches']}"
                for item in statistics['top_match_sources']
            )
            or "нет данных"
        )
        + "\n\n"
        "Количество найденных публикаций и обработанных полных текстов хранится раздельно."
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="web:registry")]]
        ),
    )
    await callback.answer()
