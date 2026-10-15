import asyncio
import logging

import betterlogging as bl
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands
from fastapi import FastAPI

from tgbot.config import load_config
from tgbot.db.db import close_db, get_engine, init_db
from tgbot.handlers import admin_router, user_router
from tgbot.middlewares.config import ConfigMiddleware
from tgbot.webhooks import yookassa_router

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO"):
    bl.basic_colorized_config(level=getattr(logging, log_level.upper(), logging.INFO))
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
    )


def create_app(config) -> FastAPI:
    app = FastAPI(title="YooKassa Webhook")
    app.include_router(yookassa_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


async def on_startup(bot: Bot, admin_ids: list[int]):
    commands = [
        BotCommand(command="start", description="Перезапуск бота"),
        BotCommand(command="admin", description="Админ-панель"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def register_global_middlewares(dp: Dispatcher, config):
    middleware_types = [
        ConfigMiddleware(config),
    ]
    for middleware_type in middleware_types:
        dp.message.outer_middleware(middleware_type)
        dp.callback_query.outer_middleware(middleware_type)


async def main():
    config = load_config(".env")
    setup_logging(config.misc.log_level)

    # Initialize database
    get_engine(config.db)
    await init_db(config.db)

    # Bot setup
    storage = MemoryStorage()
    bot = Bot(
        token=config.tg_bot.token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher(storage=storage)

    # Register routers (admin first, then user)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    register_global_middlewares(dp, config)

    # Webhook server
    app = create_app(config)
    webhook_config = uvicorn.Config(
        app,
        host=config.webhook.host,
        port=config.webhook.port,
        log_level=config.misc.log_level.lower(),
    )
    server = uvicorn.Server(webhook_config)

    # Start bot and webhook server
    await on_startup(bot, config.tg_bot.admin_ids)

    bot_task = asyncio.create_task(dp.start_polling(bot))
    server_task = asyncio.create_task(server.serve())

    logger.info("Bot started. Webhook server on port %d", config.webhook.port)

    try:
        await asyncio.gather(bot_task, server_task)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен!")
