import asyncio
import logging
import betterlogging as bl
from sqlalchemy import delete, select

from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from tgbot.config import load_config, Config
from tgbot.db.db import AsyncSessionLocal, init_db
from tgbot.db.models import AdminCommandScopeModel
from tgbot.handlers import user_router
from tgbot.handlers.statistics import statistics_router
from tgbot.middlewares.config import ConfigMiddleware
from tgbot.services.payment import payment_service



async def _get_saved_admin_ids() -> set[int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminCommandScopeModel.admin_id)
        )
        return set(result.scalars().all())


async def _save_admin_ids(admin_ids: set[int]) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AdminCommandScopeModel))
        session.add_all(
            AdminCommandScopeModel(admin_id=admin_id)
            for admin_id in sorted(admin_ids)
        )
        await session.commit()


async def on_startup(bot: Bot, admin_ids: list[int]):
    current_admin_ids = {int(admin_id) for admin_id in admin_ids}
    try:
        saved_admin_ids = await _get_saved_admin_ids()
    except Exception:
        logging.exception("Не удалось прочитать сохранённые меню администраторов")
        saved_admin_ids = set()

    commands = [
        BotCommand(
            command="start",
            description="Перезапуск бота",
        ),
    ]

    await bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault(),
    )

    synchronization_failed = False
    removed_admin_ids = saved_admin_ids - current_admin_ids
    removed_admin_ids.add(599033114)
    for removed_admin_id in sorted(removed_admin_ids):
        try:
            await bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=removed_admin_id)
            )
        except Exception:
            synchronization_failed = True
            logging.exception(
                "Не удалось удалить меню администратора %s",
                removed_admin_id,
            )

    for admin_id in sorted(current_admin_ids):
        try:
            await bot.set_my_commands(
                commands + [
                    BotCommand(
                        command="stat",
                        description="Статистика продаж",
                    ),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            synchronization_failed = True
            logging.exception(
                "Не удалось установить меню администратора %s",
                admin_id,
            )

    await bot.set_chat_menu_button(
        menu_button=MenuButtonCommands(),
    )

    if synchronization_failed:
        return

    try:
        await _save_admin_ids(current_admin_ids)
    except Exception:
        logging.exception("Не удалось сохранить меню администраторов")



def register_global_middlewares(dp: Dispatcher, config: Config, session_pool=None):
    middleware_types = [
        ConfigMiddleware(config),
    ]

    for middleware_type in middleware_types:
        dp.message.outer_middleware(middleware_type)
        dp.callback_query.outer_middleware(middleware_type)


def setup_logging():
    log_level = logging.INFO
    bl.basic_colorized_config(level=log_level)
    logging.basicConfig(
        level=logging.INFO,
        format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting bot")


async def main():
    setup_logging()
    config = load_config(".env")
    storage = MemoryStorage()
    bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode='HTML'))
    dp = Dispatcher(storage=storage)

    dp.include_router(statistics_router)
    dp.include_router(user_router)
    register_global_middlewares(dp, config)

    await init_db()
    # Recovery performs only a short DB lookup and schedules each monitor in
    # its own asyncio task; it must not delay polling startup.
    payment_service.schedule_recovery(bot)
    await on_startup(bot, config.tg_bot.admin_ids)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.error("Бот остановлен!")
