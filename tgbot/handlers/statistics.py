import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional, Tuple

import pytz
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from tgbot.config import Config
from tgbot.db.analytics import AnalyticsService, parse_source_filter
from tgbot.db.db import AsyncSessionLocal


logger = logging.getLogger(__name__)
statistics_router = Router()
MOSCOW = pytz.timezone("Europe/Moscow")
PERIODS = {"today", "7d", "30d"}


def period_bounds(period: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
    if period is None or period == "all":
        return None, None
    now = datetime.now(MOSCOW)
    local_today = now.date()
    if period == "today":
        first_day = local_today
    elif period == "7d":
        first_day = local_today - timedelta(days=6)
    elif period == "30d":
        first_day = local_today - timedelta(days=29)
    else:
        raise ValueError("unknown period")

    start_local = MOSCOW.localize(datetime.combine(first_day, time.min))
    end_local = start_local + timedelta(days=1 if period == "today" else (7 if period == "7d" else 30))
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def period_label(period: Optional[str]) -> str:
    if period is None or period == "all":
        return "всё время"
    if period == "today":
        return "сегодня (Europe/Moscow)"
    if period == "7d":
        return "последние 7 календарных дней (Europe/Moscow)"
    return "последние 30 календарных дней (Europe/Moscow)"


def format_money(value: Decimal) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    text = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    if text.endswith(",00"):
        text = text[:-3]
    return text


def format_percent(value: float) -> str:
    return f"{value:.1f}%"


def parse_arguments(args: Optional[str]):
    parts = (args or "").split()
    if not parts:
        return None, None
    if len(parts) > 2:
        raise ValueError("too many arguments")
    first = parts[0].lower()
    second = parts[1].lower() if len(parts) == 2 else None

    if first.startswith("source:"):
        source = first[len("source:") :]
        if not source:
            raise ValueError("empty source filter")
        if second is not None and second not in PERIODS and second != "all":
            raise ValueError("unknown period")
        return source, (None if second in (None, "all") else second)

    if first in PERIODS:
        if second is not None:
            raise ValueError("period must be the only argument")
        return None, first
    source = None if first == "all" else first
    if second is not None and second not in PERIODS and second != "all":
        raise ValueError("unknown period")
    return source, (None if second in (None, "all") else second)


async def send_long_text(message, text: str) -> None:
    limit = 3900
    buffer = ""

    for line in text.splitlines():
        while len(line) > limit:
            if buffer:
                await message.answer(buffer, parse_mode=None)
                buffer = ""
            await message.answer(line[:limit], parse_mode=None)
            line = line[limit:]

        candidate = f"{buffer}\n{line}" if buffer else line
        if len(candidate) > limit:
            if buffer:
                await message.answer(buffer, parse_mode=None)
            buffer = line
        else:
            buffer = candidate

    if buffer:
        await message.answer(buffer, parse_mode=None)


def _event(metrics, name: str) -> int:
    return int(metrics["events"].get(name, 0))


def render_overall(metrics, sources, period):
    lines = [
        "📊 СТАТИСТИКА ПРОДАЖ",
        "",
        f"Период: {period_label(period)}",
        "",
        f"👥 Уникальных пользователей: {metrics['users']}",
        f"🔗 Всего переходов: {metrics['visits']}",
        f"💳 Покупок за период: {metrics['payments']}",
        f"💰 Выручка за период: {format_money(metrics['revenue'])} ₽",
        "📈 Конверсия когорты переходов "
        f"(включая поздние оплаты): {format_percent(metrics['conversion'])}",
        "",
        "Воронка:",
        f"🚀 Запустили бота: {_event(metrics, 'bot_started')}",
        f"✅ Подтвердили согласие: {_event(metrics, 'consent_accepted')}",
        f"📖 Получили описание: {_event(metrics, 'description_received')}",
        f"📧 Ввели email: {_event(metrics, 'email_entered')}",
        f"💳 Получили ссылку на оплату: {_event(metrics, 'payment_link_sent')}",
        f"💰 Оплатили: {_event(metrics, 'payment_succeeded')}",
        f"🔓 Получили доступ: {_event(metrics, 'access_granted')}",
        "",
        "Источники:",
    ]
    for row in sources:
        lines.extend(
            [
                row["name"],
                f"👥 {row['users']} | 💳 {row['payments']} покупок за период | "
                f"💰 {format_money(row['revenue'])} ₽ за период",
            ]
        )
    return "\n".join(lines)


def render_campaign(metrics, source, period):
    return "\n".join(
        [
            f"📊 КАМПАНИЯ: {source}",
            "",
            f"Период: {period_label(period)}",
            "",
            f"👥 Пользователей: {metrics['users']}",
            f"🔗 Переходов: {metrics['visits']}",
            f"✅ Согласились: {_event(metrics, 'consent_accepted')}",
            f"📖 Получили описание: {_event(metrics, 'description_received')}",
            f"📧 Ввели email: {_event(metrics, 'email_entered')}",
            f"💳 Получили ссылку на оплату: {_event(metrics, 'payment_link_sent')}",
            f"💰 Покупок за период: {metrics['payments']}",
            f"🔓 Получили доступ: {_event(metrics, 'access_granted')}",
            "",
            f"💵 Выручка за период: {format_money(metrics['revenue'])} ₽",
            "📈 Конверсия когорты (включая поздние оплаты): "
            f"{format_percent(metrics['conversion'])}",
        ]
    )


@statistics_router.message(Command("stat"))
async def statistics_command(
    message: Message,
    command: CommandObject,
    config: Config,
):
    if (
        message.from_user is None
        or message.from_user.id not in config.tg_bot.admin_ids
        or message.chat.type != "private"
    ):
        return

    try:
        source_filter, period = parse_arguments(command.args)
        start, end = period_bounds(period)
    except ValueError:
        await message.answer(
            "Формат: /stat, /stat youtube, /stat youtube* 7d, "
            "/stat source:today 7d"
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            analytics = AnalyticsService(session)
            if source_filter is None:
                metrics = await analytics.get_funnel_statistics(None, start, end)
                sources = await analytics.get_source_statistics(start, end)
                text = render_overall(metrics, sources, period)
            else:
                if not parse_source_filter(source_filter):
                    await message.answer("Статистика отсутствует.")
                    return
                if not await analytics.has_source_filter(source_filter):
                    await message.answer("Статистика отсутствует.")
                    return
                metrics = await analytics.get_funnel_statistics(
                    source_filter, start, end
                )
                text = render_campaign(metrics, source_filter, period)
    except Exception:
        logger.exception("Ошибка формирования статистики")
        await message.answer("Не удалось получить статистику. Попробуйте позже.")
        return

    await send_long_text(message, text)
