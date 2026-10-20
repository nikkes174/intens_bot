from __future__ import annotations

import asyncio
import logging

from app.admin.bot import create_admin_bot, create_dispatcher
from app.config import get_settings
from app.database import init_database, close_database
from app.services.monitor import (
    configure,
    notification_worker,
    periodic_recovery,
    recover_pending_notifications,
)
from app.userbot import sources as source_service
from app.userbot.client import create_telethon_client
from app.userbot.handlers import register_handlers
from app.services.monitor import process_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_stopping = False


async def _run_telethon(client) -> None:
    global _stopping
    backoff = 1
    max_backoff = 60
    while not _stopping:
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "Telethon session is not authorized. Run scripts/login.py first."
                )
            logger.info("Telethon connected")
            await recover_pending_notifications()
            await client.run_until_disconnected()
            if _stopping:
                logger.info("Telethon stopped")
                return
            logger.warning("Telethon disconnected unexpectedly, reconnecting")
            backoff = 1
        except asyncio.CancelledError:
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Telethon connection error: %s", exc)
            try:
                await client.disconnect()
            except Exception:
                pass
            if _stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


async def main() -> None:
    global _stopping
    settings = get_settings()
    await init_database()

    bot = create_admin_bot()
    dispatcher = create_dispatcher()
    client = create_telethon_client()
    source_service.set_client(client)
    configure(
        bot=bot,
        admin_id=settings.admin_id,
        client=client,
    )

    register_handlers(client, process_message)

    worker_task = asyncio.create_task(notification_worker())
    recovery_task = asyncio.create_task(periodic_recovery())
    telethon_task = asyncio.create_task(_run_telethon(client))
    polling_task = asyncio.create_task(dispatcher.start_polling(bot))

    try:
        done, pending = await asyncio.wait(
            {worker_task, telethon_task, polling_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
        if telethon_task in done and not telethon_task.cancelled():
            await polling_task
    except Exception as exc:
        logger.exception("Application stopped: %s", exc)
        raise
    finally:
        _stopping = True
        for task in (worker_task, recovery_task, telethon_task, polling_task):
            task.cancel()
        for task in (worker_task, recovery_task, telethon_task, polling_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await client.disconnect()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
