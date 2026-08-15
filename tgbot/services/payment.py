import asyncio
import os
from contextlib import suppress
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from yookassa import Configuration, Payment

from config import PAYMENT_AMOUNT
from tgbot.db.crud_users import UserCrud
from tgbot.db.db import AsyncSessionLocal


load_dotenv()


class PaymentService:

    def __init__(self) -> None:
        self.shop_id = os.getenv("YOOKASSA_SHOP_ID")
        self.secret_key = os.getenv("YOOKASSA_SECRET_KEY")
        self.return_url = os.getenv("YOOKASSA_RETURN_URL")
        self.channel_id = os.getenv("CHANNEL_CHAT_ID")
        self.amount = PAYMENT_AMOUNT
        self._tasks: set[asyncio.Task] = set()

    def _configure_yookassa(self) -> None:
        if not self.shop_id or not self.secret_key:
            raise RuntimeError("YooKassa credentials are not configured")
        Configuration.account_id = self.shop_id
        Configuration.secret_key = self.secret_key

    def _get_channel_id(self) -> int:
        if not self.channel_id:
            raise RuntimeError("CHANNEL_CHAT_ID is not configured")
        return int(self.channel_id)

    def is_payment_channel(self, chat_id: int) -> bool:
        return chat_id == self._get_channel_id()

    async def create_payment(
        self, user_id: int, username: Optional[str]
    ) -> tuple[str, str]:
        self._configure_yookassa()
        payload = {
            "amount": {"value": self.amount, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": self.return_url or "https://t.me/",
            },
            "capture": True,
            "description": f"Подписка для пользователя {user_id}",
            "metadata": {"user_id": str(user_id)},
        }
        payment = await asyncio.to_thread(Payment.create, payload)

        async with AsyncSessionLocal() as session:
            await UserCrud(session).set_payment_pending(user_id, username, payment.id)

        return payment.id, payment.confirmation.confirmation_url

    def start_payment_monitor(self, payment_id: str, user_id: int, bot: Bot) -> None:
        task = asyncio.create_task(self._wait_for_payment(payment_id, user_id, bot))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wait_for_payment(self, payment_id: str, user_id: int, bot: Bot) -> None:

        for _ in range(120):
            try:
                self._configure_yookassa()
                payment = await asyncio.to_thread(Payment.find_one, payment_id)
            except Exception:
                await asyncio.sleep(15)
                continue

            if payment.status == "succeeded":
                async with AsyncSessionLocal() as session:
                    saved = await UserCrud(session).mark_payment_succeeded(user_id, payment_id)
                if saved:
                    await self._send_join_request_link(user_id, bot)
                return

            if payment.status == "canceled":
                return
            await asyncio.sleep(15)

    async def _send_join_request_link(self, user_id: int, bot: Bot) -> None:
        invite = await bot.create_chat_invite_link(
            chat_id=self._get_channel_id(),
            name=f"payment-{user_id}",
            creates_join_request=True,
        )
        await bot.send_message(
            user_id,
            "✅Оплата подтверждена✅\n 👇🏾Перейдите по ссылке и отправьте заявку на вступление в канал:\n"
            f"{invite.invite_link}",
        )

    async def approve_join_request(self, join_request, bot: Bot) -> bool:
        async with AsyncSessionLocal() as session:
            is_paid = await UserCrud(session).has_successful_payment(join_request.from_user.id)

        if is_paid:
            await join_request.approve()
            invite_link = join_request.invite_link.invite_link
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Перейти", url=invite_link)
            ]])
            await bot.send_message(
                join_request.from_user.id,
                "🎉Вы были приняты в канал🎉",
                reply_markup=keyboard,
            )
            return True

        with suppress(Exception):
            await join_request.decline()
        return False


payment_service = PaymentService()
