import asyncio
import logging
import os
from contextlib import suppress
from decimal import Decimal
from typing import Dict, Optional, Set, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from yookassa import Configuration, Payment

from config import PAYMENT_AMOUNT
from tgbot.db.crud_users import UserCrud
from tgbot.db.analytics import AnalyticsService
from tgbot.db.db import AsyncSessionLocal
from tgbot.db.models import PaymentAnalyticsModel
from sqlalchemy import select


load_dotenv()

logger = logging.getLogger(__name__)


class PaymentService:

    def __init__(self) -> None:
        self.shop_id = os.getenv("YOOKASSA_SHOP_ID")
        self.secret_key = os.getenv("YOOKASSA_SECRET_KEY")
        self.return_url = os.getenv("YOOKASSA_RETURN_URL")
        self.channel_id = os.getenv("CHANNEL_CHAT_ID")
        self.amount = PAYMENT_AMOUNT
        self.vat_code = os.getenv("YOOKASSA_VAT_CODE")
        self._tasks: Set[asyncio.Task] = set()
        self._payment_tasks: Dict[str, asyncio.Task] = {}

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
        self,
        user_id: int,
        username: Optional[str],
        email: str,
        visit_id: Optional[int] = None,
    ) -> Tuple[str, str]:
        self._configure_yookassa()
        if not self.vat_code:
            raise RuntimeError("YOOKASSA_VAT_CODE is not configured")
        try:
            vat_code = int(self.vat_code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("YOOKASSA_VAT_CODE must be an integer") from exc
        payload = {
            "amount": {"value": self.amount, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": self.return_url or "https://t.me/",
            },
            "capture": True,
            "description": f"Подписка для пользователя {user_id}",
            "metadata": {"user_id": str(user_id)},
            "receipt": {
                "customer": {"email": email},
                "items": [{
                    "description": "Подписка на закрытый канал на 1 месяц",
                    "quantity": "1.00",
                    "amount": {"value": self.amount, "currency": "RUB"},
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                    "vat_code": vat_code,
                }],
            },
        }
        payment = await asyncio.to_thread(Payment.create, payload)

        # Keep the existing users/payment_id flow for compatibility.
        async with AsyncSessionLocal() as session:
            await UserCrud(session).set_payment_pending(
                user_id, username, payment.id
            )

        # This write is intentionally isolated: once YooKassa has created a
        # payment, an analytics outage must not make the handler create
        # another payment on retry.

        try:
            amount, currency = self._payment_amount(payment)
            async with AsyncSessionLocal() as session:
                await AnalyticsService(session).create_payment_record(
                    payment_id=payment.id,
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    visit_id=visit_id,
                )
        except Exception:
            logger.exception(
                "Не удалось сохранить аналитику платежа %s; платёж уже создан",
                payment.id,
            )

        return payment.id, payment.confirmation.confirmation_url

    @staticmethod
    def _payment_amount(payment) -> Tuple[Decimal, str]:
        amount_object = getattr(payment, "amount", None)
        value = getattr(amount_object, "value", None)
        currency = getattr(amount_object, "currency", None) or "RUB"
        if value is None:
            raise RuntimeError("YooKassa did not return the created payment amount")
        return Decimal(str(value)), str(currency)

    def start_payment_monitor(
        self,
        payment_id: str,
        user_id: int,
        bot: Bot,
        visit_id: Optional[int] = None,
    ) -> None:
        existing = self._payment_tasks.get(payment_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._wait_for_payment(payment_id, user_id, bot, visit_id)
        )
        self._tasks.add(task)
        self._payment_tasks[payment_id] = task

        def cleanup(completed_task: asyncio.Task) -> None:
            self._tasks.discard(completed_task)
            if self._payment_tasks.get(payment_id) is completed_task:
                self._payment_tasks.pop(payment_id, None)

        task.add_done_callback(cleanup)

    def schedule_recovery(self, bot: Bot) -> None:
        task = asyncio.create_task(self.recover_pending_payments(bot))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wait_for_payment(
        self,
        payment_id: str,
        user_id: int,
        bot: Bot,
        visit_id: Optional[int] = None,
    ) -> None:

        for _ in range(120):
            try:
                self._configure_yookassa()
                payment = await asyncio.to_thread(Payment.find_one, payment_id)
            except Exception:
                await asyncio.sleep(15)
                continue

            if payment.status == "succeeded":
                analytics_first = False
                try:
                    async with AsyncSessionLocal() as session:
                        analytics = AnalyticsService(session)
                        record = await analytics.get_payment(payment_id)
                        if record is None:
                            amount, currency = self._payment_amount(payment)
                            await analytics.create_payment_record(
                                payment_id=payment_id,
                                user_id=user_id,
                                amount=amount,
                                currency=currency,
                                visit_id=visit_id,
                            )
                        analytics_first = await analytics.mark_payment_success(
                            payment_id
                        )
                except Exception:
                    logger.exception(
                        "Не удалось обновить аналитику успешного платежа %s",
                        payment_id,
                    )

                try:
                    async with AsyncSessionLocal() as session:
                        saved = await UserCrud(session).mark_payment_succeeded(
                            user_id, payment_id
                        )
                except Exception:
                    logger.exception(
                        "Не удалось обновить legacy-статус платежа %s", payment_id
                    )
                    saved = False

                if saved or analytics_first:
                    await self._send_join_request_link(user_id, bot, payment_id)
                return

            if payment.status == "canceled":
                try:
                    async with AsyncSessionLocal() as session:
                        analytics = AnalyticsService(session)
                        record = await analytics.get_payment(payment_id)
                        if record is None:
                            amount, currency = self._payment_amount(payment)
                            await analytics.create_payment_record(
                                payment_id=payment_id,
                                user_id=user_id,
                                amount=amount,
                                currency=currency,
                                visit_id=visit_id,
                                status="canceled",
                            )
                        else:
                            await analytics.mark_payment_canceled(payment_id)
                except Exception:
                    logger.exception(
                        "Не удалось обновить аналитику отменённого платежа %s",
                        payment_id,
                    )
                return
            await asyncio.sleep(15)

    async def _send_join_request_link(
        self, user_id: int, bot: Bot, payment_id: Optional[str] = None
    ) -> None:
        if payment_id is not None:
            try:
                async with AsyncSessionLocal() as session:
                    if await AnalyticsService(session).has_event(
                        user_id, "access_link_sent", payment_id
                    ):
                        return
            except Exception:
                logger.exception(
                    "Не удалось проверить выдачу ссылки для %s", payment_id
                )

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
        if payment_id is not None:
            try:
                async with AsyncSessionLocal() as session:
                    payment = await AnalyticsService(session).get_payment(payment_id)
                    await AnalyticsService(session).log_event(
                        user_id=user_id,
                        event="access_link_sent",
                        visit_id=payment.visit_id if payment else None,
                        payment_id=payment_id,
                    )
            except Exception:
                logger.exception(
                    "Не удалось записать access_link_sent для %s", payment_id
                )

    async def approve_join_request(self, join_request, bot: Bot) -> bool:
        async with AsyncSessionLocal() as session:
            is_paid = await UserCrud(session).has_successful_payment(join_request.from_user.id)

        if is_paid:
            await join_request.approve()
            try:
                async with AsyncSessionLocal() as session:
                    analytics = AnalyticsService(session)
                    payment = await analytics.get_latest_successful_payment(
                        join_request.from_user.id
                    )
                    if payment is None:
                        already_logged = await analytics.has_event(
                            join_request.from_user.id, "access_granted"
                        )
                        if not already_logged:
                            await analytics.log_event(
                                join_request.from_user.id, "access_granted"
                            )
                    elif not await analytics.has_event(
                        join_request.from_user.id,
                        "access_granted",
                        payment.payment_id,
                    ):
                        await analytics.log_event(
                            user_id=join_request.from_user.id,
                            event="access_granted",
                            visit_id=payment.visit_id,
                            payment_id=payment.payment_id,
                        )
            except Exception:
                logger.exception("Не удалось записать access_granted")
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

    async def recover_pending_payments(self, bot: Bot) -> None:
        """Schedule monitors for payments that outlived the process."""

        payment_users = {}
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(PaymentAnalyticsModel.payment_id, PaymentAnalyticsModel.user_id)
                    .where(PaymentAnalyticsModel.status == "pending")
                )
                payment_users.update(result.all())

                # Payments created by an older version have no analytics row.
                # Keep their legacy monitor behavior instead of abandoning
                # them after the migration.
                for user in await UserCrud(session).get_users_list():
                    if user.payment_id and not user.payment_ok:
                        payment_users.setdefault(user.payment_id, user.user_id)
        except Exception:
            logger.exception("Не удалось восстановить pending-платежи")
            return

        for payment_id, user_id in payment_users.items():
            visit_id = None
            try:
                async with AsyncSessionLocal() as session:
                    visit_id = await AnalyticsService(session).get_payment_visit_id(
                        payment_id
                    )
            except Exception:
                logger.exception("Не удалось восстановить визит платежа %s", payment_id)
            self.start_payment_monitor(payment_id, user_id, bot, visit_id=visit_id)


payment_service = PaymentService()
