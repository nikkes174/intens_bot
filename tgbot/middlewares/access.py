from collections.abc import Awaitable, Callable, Collection
from typing import Any

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject, Update


class AccessMiddleware(BaseMiddleware):
    """Allow updates only from users listed in ADMINS_IDS."""

    def __init__(self, allowed_user_ids: Collection[int]) -> None:
        self.allowed_user_ids = frozenset(allowed_user_ids)

    @staticmethod
    def _get_user_id(update: Update) -> int | None:
        try:
            event = update.event
        except Exception:
            return None

        user = getattr(event, "from_user", None) or getattr(event, "user", None)
        user_id = getattr(user, "id", None)
        return user_id if isinstance(user_id, int) and user_id > 0 else None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return None

        user_id = self._get_user_id(event)
        if user_id is None or user_id not in self.allowed_user_ids:
            return None
        return await handler(event, data)
