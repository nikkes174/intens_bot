from __future__ import annotations

import asyncio
import logging

from telethon import events, errors

from app.services.monitor import process_message
from app.userbot.sources import ensure_source_from_event

logger = logging.getLogger(__name__)


def register_handlers(client, process_message_func=None):
    if process_message_func is None:
        process_message_func = process_message

    async def _process_single(source_id, message_id, text, event):
        if not text:
            return False
        await ensure_source_from_event(event)
        try:
            return await process_message_func(
                source_id=source_id,
                message_id=message_id,
                text=text,
                event=event,
            )
        except errors.FloodWaitError as exc:
            await asyncio.sleep(exc.seconds)
            return await process_message_func(
                source_id=source_id,
                message_id=message_id,
                text=text,
                event=event,
            )
        except (ConnectionError, TimeoutError, OSError) as exc:
            logger.warning("Network error while processing message: %s", exc)
            return False

    @client.on(events.NewMessage(incoming=True))
    async def on_new_message(event):
        try:
            if not event.message:
                return
            if getattr(event.message, "action", None) is not None:
                return
            if not (event.is_channel or event.is_group):
                return
            if getattr(event.message, "grouped_id", None) is not None:
                return

            text = event.raw_text
            if not text:
                return
            await _process_single(
                event.chat_id,
                event.message.id,
                text,
                event,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unhandled error in Telegram handler: %s", exc)

    @client.on(events.Album)
    async def on_album(event):
        try:
            if not (event.is_channel or event.is_group):
                return
            messages = getattr(event, "messages", None) or []
            if not messages:
                return
            texts = [m.raw_text for m in messages if getattr(m, "raw_text", None)]
            text = "\n".join(texts)
            if not text:
                return
            await _process_single(
                event.chat_id,
                messages[0].id,
                text,
                messages[0],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unhandled error in album handler: %s", exc)

    return on_new_message
