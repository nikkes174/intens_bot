from __future__ import annotations

import asyncio
import logging

from app.admin.bot import create_admin_bot, create_dispatcher
from app.config import get_settings
from app.database import init_database, close_database
from app.services.monitor import (
    configure,
    notification_worker,
    recover_pending_notifications,
)
from app.userbot.client import create_telethon_client
from app.userbot.handlers import register_handlers
from app.services.monitor import process_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    await init_database()

    bot = create_admin_bot()
    dispatcher = create_dispatcher()

    client = create_telethon_client()
    configure(
        bot=bot,
        admin_id=settings.admin_ids[0] if settings.admin_ids else None,
        client=client,
    )

    register_handlers(client, process_message)

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telethon session is not authorized. Run scripts/login.py first."
        )

    await recover_pending_notifications()
    worker_task = asyncio.create_task(notification_worker())

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot),
            client.run_until_disconnected(),
        )
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await client.disconnect()
        await bot.session.close()
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
