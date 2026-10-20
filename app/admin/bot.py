from __future__ import annotations

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from app.admin.keyboards import main_keyboard
from app.config import get_settings, is_admin
from app.services.monitor import monitoring_enabled


def create_admin_bot() -> Bot:
    settings = get_settings()
    return Bot(
        token=settings.bot_token,
    )


def create_dispatcher() -> Dispatcher:
    from app.admin.mentions import router as mentions_router
    from app.admin.channels import router as channels_router
    from app.admin.settings import router as settings_router

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.include_router(mentions_router)
    dp.include_router(channels_router)
    dp.include_router(settings_router)
    return dp


router = Router()


def _admin_only(user_id: int) -> bool:
    return is_admin(user_id)


@router.message(CommandStart())
async def cmd_start(message: Message):
    if not _admin_only(message.from_user.id):
        await message.answer("Доступ запрещён.")
        return
    status = "включён" if await monitoring_enabled() else "выключен"
    await message.answer(
        f"Telegram-мониторинг.\nСостояние: {status}",
        reply_markup=main_keyboard(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    status = "включён" if await monitoring_enabled() else "выключен"
    await callback.message.edit_text(
        f"Telegram-мониторинг.\nСостояние: {status}",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "stats:show")
async def stats_show(callback: CallbackQuery):
    if not _admin_only(callback.from_user.id):
        await callback.answer("Доступ запрещён", show_alert=True)
        return
    from app.admin.statistics import render_stats

    text, keyboard = await render_stats()
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def create_bot(*args, **kwargs):
    return create_admin_bot(*args, **kwargs)
