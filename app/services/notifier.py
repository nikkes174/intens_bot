from __future__ import annotations
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup


async def send_notification(
    bot: Bot,
    admin_id: int,
    text: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
):
    try:
        await bot.send_message(
            chat_id=admin_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return True
    except TelegramRetryAfter:
        raise
    except TelegramForbiddenError:
        raise
