import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from tgbot.config import ConfigError, Settings, load_config
from tgbot.handlers.photo import router as photo_router
from tgbot.handlers.start import router as start_router
from tgbot.middlewares.access import AccessMiddleware
from tgbot.services.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(filename)s:%(lineno)d #%(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
    )


async def configure_bot(bot: Bot) -> None:
    await bot.set_my_commands(
        [BotCommand(command="start", description="Начать заново")],
        scope=BotCommandScopeDefault(),
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def create_dispatcher(settings: Settings, openrouter: OpenRouterClient) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["settings"] = settings
    dispatcher["openrouter"] = openrouter
    dispatcher.update.outer_middleware(AccessMiddleware(settings.admins_ids))
    dispatcher.include_router(start_router)
    dispatcher.include_router(photo_router)
    return dispatcher


async def main() -> None:
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    settings = load_config()
    setup_logging(settings.log_level)

    openrouter = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        allow_paid=settings.openrouter_allow_paid,
        base_url=settings.openrouter_base_url,
        timeout=settings.openrouter_timeout,
    )
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dispatcher = create_dispatcher(settings, openrouter)

    try:
        await configure_bot(bot)
        logger.info("Бот запущен")
        await dispatcher.start_polling(bot)
    finally:
        await openrouter.close()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as error:
        setup_logging(os.getenv("LOG_LEVEL", "INFO"))
        logger.error("Ошибка конфигурации: %s", error)
        raise SystemExit(1) from error
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
