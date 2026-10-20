from __future__ import annotations

import asyncio
import logging

from telethon import events, errors

from app.services.monitor import process_message
from app.userbot.sources import ensure_source_from_event

logger = logging.getLogger(__name__)

_processed_groups: set[tuple[int, int]] = set()


def register_handlers(client, process_message_func=None):
    if process_message_func is None:
        process_message_func = process_message

    @client.on(events.NewMessage(incoming=True))
    async def on_new_message(event):
        try:
            if not event.message:
                return
            if getattr(event.message, "action", None) is not None:
                return
            if not (event.is_channel or event.is_group):
                return

            text = event.raw_text
            if not text:
                return

            source_id = event.chat_id
            message_id = event.message.id

            await ensure_source_from_event(event)

            grouped_id = getattr(event.message, "grouped_id", None)
            if grouped_id is not None:
                key = (source_id, int(grouped_id))
                if key in _processed_groups:
                    return

            try:
                result = await process_message_func(
                    source_id=source_id,
                    message_id=message_id,
                    text=text,
                    event=event,
                )
            except errors.FloodWaitError as exc:
                await asyncio.sleep(exc.seconds)
                result = await process_message_func(
                    source_id=source_id,
                    message_id=message_id,
                    text=text,
                    event=event,
                )
            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.warning("Network error while processing message: %s", exc)
                return

            if grouped_id is not None and result:
                _processed_groups.add((source_id, int(grouped_id)))

        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unhandled error in Telegram handler: %s", exc)

    return on_new_message
